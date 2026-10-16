import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from reconcile_article_audit import reconcile_payloads, permitted_nonclaim_unit, nonclaim_frame
from audit_preflight import freeze_inventories


def payload(text, category='transition', signal='condition_or_scope', second=True):
    unit = dict(unit_id='U001', line=1, text=text, unit_type='content', claim_review_signals=[signal])
    classification = dict(unit_id='U001', classification='non_factual', category=category, reason='本句仅引入后文，不独立陈述事实。')
    first = dict(article_id='ART-1', evaluation_protocol_version='2.0', claims=[],
                 coverage_review=dict(unit_classifications=[copy.deepcopy(classification)]))
    blind = dict(article_id='ART-1', evaluation_protocol_version='2.0', claim_reviews=[], claim_inventory=[],
                 coverage_review=dict(unit_classifications=[copy.deepcopy(classification)] if second else []))
    return {'article_id': 'ART-1', 'article_units': [unit]}, first, blind


class DiscourseRegressionTests(unittest.TestCase):
    def test_both_passes_empty_frames_do_not_reenter_denominator(self):
        for text, signal, category in (
            ('The following concepts are starting points for discussion, with questions to resolve before adoption.', 'condition_or_scope', 'transition'),
            ('Environmental language needs the same care as the material choice.', 'comparison', 'pure_recommendation'),
            ('Before expanding the collection, consider agreeing on:', 'condition_or_scope', 'transition'),
            ('Before extending the range, consider agreeing on:', 'operational_method', 'transition'),
        ):
            with self.subTest(text=text):
                p, a, b = payload(text, category, signal)
                self.assertTrue(permitted_nonclaim_unit(p['article_units'][0], a['coverage_review']['unit_classifications'][0], b['coverage_review']['unit_classifications'][0]))
                result = reconcile_payloads(a, b, p)
                self.assertEqual(a['claims'], [])
                self.assertEqual(result['coverage_fallback_unit_ids'], [])

    def test_missing_or_disagreeing_blind_pass_retains_reviewable_frame(self):
        for second in ('missing', 'factual'):
            p, a, b = payload('Before expanding the collection, consider agreeing on:', second=second != 'missing')
            if second == 'factual':
                b['coverage_review']['unit_classifications'][0]['classification'] = 'factual'
            reconcile_payloads(a, b, p)
            self.assertEqual(len(a['claims']), 1)
            self.assertEqual(a['claims'][0]['semantic_status'], 'unknown')

    def test_real_methods_effects_quantities_not_exempt_even_if_transition(self):
        for text in ('Before expanding the collection, test the insert to reduce damage.',
                     'The following concepts improve protection by 30%.',
                     'Before expanding the collection, agree on 3 tested formats.',
                     'Environmental language improves purchasing accuracy.'):
            p, a, b = payload(text)
            self.assertFalse(nonclaim_frame(text))
            reconcile_payloads(a, b, p)
            self.assertEqual(len(a['claims']), 1)

    def test_question_fallback_removed_but_answer_kept(self):
        p, a, b = payload('How can a box protect the product?', 'question', second=False)
        answer = 'A rigid insert reduces product movement.'
        p['article_units'].append(dict(unit_id='U002', line=2, text=answer, unit_type='content', claim_review_signals=['effect_or_causal_relation']))
        a['coverage_review']['unit_classifications'].append(dict(unit_id='U002', classification='non_factual', category='pure_recommendation'))
        result = reconcile_payloads(a, b, p)
        self.assertEqual([c['unit_id'] for c in a['claims']], ['U002'])
        self.assertNotIn('U001', result['coverage_fallback_unit_ids'])
        again = reconcile_payloads(a, b, p)
        self.assertEqual([c['unit_id'] for c in a['claims']], ['U002'])

    def test_list_items_still_reviewed_individually(self):
        p, a, b = payload('Before expanding the collection, consider agreeing on:')
        for index, text in enumerate(('a logo treatment for all formats.', 'a shared color system.'), 2):
            uid = f'U{index:03d}'
            p['article_units'].append(dict(unit_id=uid, line=index, text=text, unit_type='content', claim_review_signals=['operational_method']))
            a['coverage_review']['unit_classifications'].append(dict(unit_id=uid, classification='non_factual', category='pure_recommendation'))
        reconcile_payloads(a, b, p)
        self.assertEqual([c['unit_id'] for c in a['claims']], ['U002', 'U003'])

    def test_frozen_inventory_uses_same_empty_frame_rule(self):
        text = 'Before expanding the collection, consider agreeing on:'
        p, a, b = payload(text)
        row = dict(inventory_id='I001', unit_id='U001', article_line=1, article_quote=text, claim=text)
        a['claim_inventory'] = [copy.deepcopy(row)]
        b['claim_inventory'] = [copy.deepcopy(row)]
        frozen = freeze_inventories(p, a, b)
        self.assertEqual(frozen['claims'], [])


if __name__ == '__main__':
    unittest.main()
