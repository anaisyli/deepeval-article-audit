import copy
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_preflight import freeze_inventories, preflight_records, recheck_list
from prepare_article_audit import sha256_file
from reconcile_article_audit import DECISIVE_REVIEW_POLICY, REVIEW_POLICY, reconcile_payloads
from test_reconciliation import base_claim, review_row, check


class PreflightTests(unittest.TestCase):
    def inventories(self):
        texts = ['The product is IP67 certified.', 'The lid attaches one side to the base.']
        prepared = dict(article_id='ART-001', article_version='v1', article_sha256='a', knowledge_sha256=[],
                        article_units=[dict(unit_id=f'U{n:03}', line=n, text=t, unit_type='paragraph') for n,t in enumerate(texts,1)])
        def row(n):
            return dict(inventory_id=f'I{n}', unit_id=f'U{n:03}', article_line=n, article_quote=texts[n-1], claim=texts[n-1])
        coverage = dict(unit_classifications=[dict(unit_id=f'U{n:03}', classification='factual') for n in (1,2)])
        first = dict(article_id='ART-001', coverage_review=copy.deepcopy(coverage), claim_inventory=[row(1)])
        blind = dict(article_id='ART-001', coverage_review=copy.deepcopy(coverage), claim_inventory=[row(2)])
        return prepared, first, blind

    def test_both_extraction_origins_await_actual_judgment(self):
        p, a, b = self.inventories()
        result = freeze_inventories(p,a,b)
        self.assertEqual(len(result['claims']),2)
        self.assertEqual({r['inventory_origin'] for r in result['claims']}, {'first_only','blind_only'})
        self.assertTrue(all('semantic_status' not in r and 'evidence' not in r for r in result['claims']))
        # Actual new judgments can retain support for second-only claims;
        # a historical unknown is never fed to this transformation.
        c = base_claim('C001', p['article_units'][0]['text'], p['article_units'][0]['text'])
        c['inventory_origin'] = 'blind_only'
        r = review_row('C001', c['claim'], c['claim'])
        j=dict(evaluation_protocol_version='2.0',article_id='ART-001',claims=[c])
        b=dict(article_id='ART-001',claim_reviews=[r])
        reconcile_payloads(j,b)
        self.assertEqual(j['claims'][0]['semantic_status'],'entailed')

    def test_identity_dedup_does_not_use_row_number(self):
        p,a,b = self.inventories()
        b['claim_inventory'].append(copy.deepcopy(a['claim_inventory'][0]))
        b['claim_inventory'][-1]['inventory_id']='different-id'
        result=freeze_inventories(p,a,b)
        self.assertEqual(len(result['claims']),2)
        self.assertEqual(result['claims'][0]['inventory_origin'],'both')

    def test_risk_profile_union_escalates_when_either_inventory_marks_high(self):
        p,a,b=self.inventories()
        a['review_policy']=b['review_policy']=REVIEW_POLICY
        a['claim_inventory'][0].update(review_risk_level='standard',review_risk_categories=[])
        b['claim_inventory'][0].update(
            unit_id='U001', article_line=1, article_quote=p['article_units'][0]['text'],
            claim=p['article_units'][0]['text'])
        b['claim_inventory'][0].update(
            review_risk_level='high',review_risk_categories=['product_scope_or_compatibility'])
        frozen=freeze_inventories(p,a,b)
        self.assertEqual(frozen['review_policy'],REVIEW_POLICY)
        self.assertEqual(len(frozen['claims']),1)
        self.assertEqual(frozen['claims'][0]['review_risk_level'],'high')
        self.assertEqual(frozen['claims'][0]['review_risk_categories'],['product_scope_or_compatibility'])

    def test_risk_weighted_preflight_allows_standard_claim_without_second_review(self):
        p,a,b=self.inventories()
        a['review_policy']=b['review_policy']=REVIEW_POLICY
        a['claim_inventory'][0].update(review_risk_level='standard',review_risk_categories=[])
        b['claim_inventory']=[]
        b['coverage_review']['unit_classifications'][1]['classification']='non_factual'
        b['coverage_review']['unit_classifications'][1].update(category='pure_recommendation',reason='普通建议，不含可核验事实。')
        frozen=freeze_inventories(p,a,b)
        c=base_claim('C001',p['article_units'][0]['text'],p['article_units'][0]['text'])
        c.update(review_risk_level='standard',review_risk_categories=[])
        judgments=dict(article_id='ART-001',review_policy=REVIEW_POLICY,claims=[c])
        blind=dict(article_id='ART-001',review_policy=REVIEW_POLICY,claim_reviews=[])
        receipt=preflight_records(p,judgments,blind,frozen=frozen)
        self.assertTrue(receipt['ready_for_reconciliation'])
        self.assertEqual(receipt['review_tasks'],[])

    def test_risk_weighted_preflight_requires_high_risk_second_review(self):
        p,a,b=self.inventories()
        a['review_policy']=b['review_policy']=REVIEW_POLICY
        a['claim_inventory'][0].update(
            review_risk_level='high',review_risk_categories=['quantity_time_or_specification'])
        b['claim_inventory']=[]
        b['coverage_review']['unit_classifications'][1]['classification']='non_factual'
        b['coverage_review']['unit_classifications'][1].update(category='pure_recommendation',reason='普通建议，不含可核验事实。')
        frozen=freeze_inventories(p,a,b)
        c=base_claim('C001',p['article_units'][0]['text'],p['article_units'][0]['text'])
        c.update(review_risk_level='high',review_risk_categories=['quantity_time_or_specification'])
        judgments=dict(article_id='ART-001',review_policy=REVIEW_POLICY,claims=[c])
        blind=dict(article_id='ART-001',review_policy=REVIEW_POLICY,claim_reviews=[])
        with self.assertRaisesRegex(ValueError,'Only high-risk'):
            preflight_records(p,judgments,blind,frozen=frozen)

    def decisive_case(self):
        p,a,b=self.inventories()
        a['review_policy']=b['review_policy']=DECISIVE_REVIEW_POLICY
        frozen=freeze_inventories(p,a,b)
        supported=base_claim('C001',p['article_units'][0]['text'],p['article_units'][0]['text'])
        unknown=base_claim('C002',p['article_units'][1]['text'],'')
        unknown.update(unit_id='U002',article_line=2,deepeval_verdict='idk',semantic_status='unknown',
                       verdict='unsupported',evidence=[])
        judgments=dict(article_id='ART-001',review_policy=DECISIVE_REVIEW_POLICY,claims=[supported,unknown])
        blind=dict(article_id='ART-001',review_policy=DECISIVE_REVIEW_POLICY,claim_reviews=[])
        return p,frozen,judgments,blind

    def test_decisive_freeze_records_policy_and_fills_article_line(self):
        p,a,b=self.inventories()
        a['review_policy']=b['review_policy']=DECISIVE_REVIEW_POLICY
        del a['claim_inventory'][0]['article_line']
        frozen=freeze_inventories(p,a,b)
        self.assertEqual(frozen['review_policy'],DECISIVE_REVIEW_POLICY)
        self.assertEqual(frozen['claims'][0]['article_line'],1)
        self.assertTrue(all('review_risk_level' not in c for c in frozen['claims']))
        b['review_policy']=REVIEW_POLICY
        with self.assertRaisesRegex(ValueError,'same review_policy'):
            freeze_inventories(p,a,b)

    def test_decisive_preflight_rechecks_only_first_pass_conclusions(self):
        p,frozen,judgments,blind=self.decisive_case()
        receipt=preflight_records(p,judgments,blind,frozen=frozen)
        self.assertEqual(receipt['review_tasks'],[dict(claim_id='C001',cause='missing_independent_review')])
        blind['claim_reviews']=[review_row('C002',judgments['claims'][1]['claim'],'',unit_id='U002')]
        with self.assertRaisesRegex(ValueError,'Only first-pass entailed or contradicted'):
            preflight_records(p,judgments,blind,frozen=frozen)

    def test_recheck_list_carries_identity_only(self):
        _,_,judgments,_=self.decisive_case()
        listing=recheck_list(judgments)
        self.assertEqual(listing['review_policy'],DECISIVE_REVIEW_POLICY)
        self.assertEqual([c['claim_id'] for c in listing['claims']],['C001'])
        self.assertEqual(set(listing['claims'][0]),{'claim_id','unit_id','article_line','article_quote','claim'})
        judgments['review_policy']=REVIEW_POLICY
        with self.assertRaises(ValueError): recheck_list(judgments)

    def test_bad_quote_and_missing_unit_classification_rejected(self):
        for variant in ('quote','coverage'):
            p,a,b=self.inventories()
            if variant=='quote': b['claim_inventory'][0]['article_quote']='A different sentence.'
            else: b['coverage_review']['unit_classifications'].pop(0)  # U001: no blind claims, no row
            with self.assertRaisesRegex(ValueError, 'U001' if variant == 'coverage' else ''): freeze_inventories(p,a,b)

    def test_unit_with_claims_needs_no_explicit_factual_row(self):
        p,a,b=self.inventories()
        b['coverage_review']['unit_classifications'].pop()  # U002 has a blind claim
        frozen=freeze_inventories(p,a,b)
        self.assertEqual(len(frozen['claims']),2)

    def test_first_proposition_preserved_unless_complete_replacement_review(self):
        p,a,b=self.inventories()
        b['claim_inventory'][0].update(unit_id='U001', article_line=1, article_quote=p['article_units'][0]['text'],
                                       claim='The product has IP67 certification.')
        self.assertEqual(len(freeze_inventories(p,a,b)['claims']),2)
        replacement=dict(source_ref='first:I1',replacement_refs=['blind:I2'],all_predicates_preserved=True,
                         coverage_reason='独立边界复核确认同一认证关系的完整主体与谓词均已保留，不携带旧结论。')
        self.assertEqual(len(freeze_inventories(p,a,b,[replacement])['claims']),1)
        replacement['all_predicates_preserved']=False
        with self.assertRaises(ValueError): freeze_inventories(p,a,b,[replacement])

    def citation_fixture(self, root):
        path=root/'facts.md'
        path.write_text('The product is IP67 certified.\nThe product has a paper wrap.\n',encoding='utf-8')
        prepared=dict(article_id='ART-001',knowledge_files=[str(path)],knowledge_sha256=[sha256_file(path)])
        def ev(n): return dict(source_file=str(path),line_start=n,line_end=n,quote=path.read_text().splitlines()[n-1])
        c=base_claim('C001','The product is IP67 certified.',ev(1)['quote'])
        c['evidence']=[ev(1)]
        r=review_row('C001',c['claim'],ev(1)['quote'])
        r['entailment_checks']['subject_object']=check('covered','product','paper wrap')
        r['evidence']=[ev(2)]
        j=dict(article_id='ART-001',evaluation_protocol_version='2.0',claims=[c])
        b=dict(article_id='ART-001',claim_reviews=[r])
        return prepared,j,b

    def test_verified_exact_citation_omission_repaired_without_changing_verdict(self):
        with tempfile.TemporaryDirectory() as temp:
            p,j,b=self.citation_fixture(Path(temp))
            receipt=preflight_records(p,j,b)
            self.assertEqual(len(receipt['automatic_repairs']),1)
            self.assertTrue(receipt['ready_for_reconciliation'])
            self.assertEqual(j['claims'][0]['semantic_status'],'entailed')
            self.assertEqual(preflight_records(p,j,b)['automatic_repairs'],[])

    def test_citation_lines_are_located_from_a_unique_quote(self):
        with tempfile.TemporaryDirectory() as temp:
            p,j,b=self.citation_fixture(Path(temp))
            for item in (j['claims'][0]['evidence'][0], b['claim_reviews'][0]['evidence'][0]):
                item.pop('line_start'); item.pop('line_end')
            receipt=preflight_records(p,j,b)
            located=[r for r in receipt['automatic_repairs'] if r['cause']=='citation_line_located']
            self.assertEqual(len(located),2)
            self.assertEqual((j['claims'][0]['evidence'][0]['line_start'],j['claims'][0]['evidence'][0]['line_end']),(1,1))
            self.assertEqual(b['claim_reviews'][0]['evidence'][0]['line_start'],2)

    def test_ambiguous_quote_is_not_located(self):
        with tempfile.TemporaryDirectory() as temp:
            p,j,b=self.citation_fixture(Path(temp))
            item=j['claims'][0]['evidence'][0]
            item.pop('line_start'); item.pop('line_end'); item['quote']='The product'
            receipt=preflight_records(p,j,b)
            self.assertNotIn('line_start',item)
            self.assertFalse(any(r['cause']=='citation_line_located' and r['owner']=='judgments'
                                 for r in receipt['automatic_repairs']))

    def test_unverified_quote_not_added_and_one_review_then_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            p,j,b=self.citation_fixture(Path(temp))
            b['claim_reviews'][0]['evidence'][0]['quote']='Invented paper wrap.'
            r=preflight_records(p,j,b)
            self.assertEqual(r['automatic_repairs'],[])
            self.assertEqual(r['next_action'],'review_records_once')
            self.assertFalse(r['terminal'])
            r=preflight_records(p,j,b,attempt=1)
            self.assertEqual(r['next_action'],'reconcile_and_render')
            reconcile_payloads(j,b)
            self.assertEqual(j['claims'][0]['semantic_status'],'unknown')

    def test_real_quantity_and_scope_require_manual_semantic_check(self):
        with tempfile.TemporaryDirectory() as temp:
            p,j,b=self.citation_fixture(Path(temp))
            c=j['claims'][0]; r=b['claim_reviews'][0]
            c['claim']=c['article_quote']='The product uses one wrap before production.'
            for key in ('claim','article_quote','claim_fragment'): r[key]=c['claim']
            r['entailment_checks']['quantity_time_version']=check('not_applicable')
            r['entailment_checks']['scope_condition']=check('not_applicable')
            receipt=preflight_records(p,j,b)
            self.assertTrue(receipt['review_tasks'])
            self.assertEqual(r['entailment_checks']['scope_condition']['status'],'not_applicable')
            self.assertEqual(c['semantic_status'],'entailed')

    def test_unknown_and_historical_results_never_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            p,j,b=self.citation_fixture(Path(temp))
            j['claims'][0]['semantic_status']='unknown'
            preflight_records(p,j,b)
            reconcile_payloads(j,b)
            self.assertEqual(j['claims'][0]['semantic_status'],'unknown')
            with self.assertRaisesRegex(ValueError,'historical'): preflight_records(p,j,b)

    def test_frozen_denominator_requires_two_complete_judgments(self):
        p,a,b=self.inventories(); f=freeze_inventories(p,a,b)
        with self.assertRaisesRegex(ValueError,'Every frozen'):
            preflight_records(p,dict(article_id='ART-001',claims=[]),dict(article_id='ART-001',claim_reviews=[]),frozen=f)


if __name__=='__main__': unittest.main()
