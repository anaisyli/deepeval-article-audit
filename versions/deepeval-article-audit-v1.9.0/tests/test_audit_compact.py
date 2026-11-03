import copy
import hashlib
import json
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_compact import (
    AUTO_NOT_APPLICABLE, assemble_judgments, assemble_review, build_view, coverage_problems,
    normalize_inventory, required_checks,
)
from audit_preflight import freeze_inventories, recheck_list, snapshot_conflicts
from prepare_article_audit import managed_task_problem, sha256_file
from reconcile_article_audit import validate_entailed_review
from render_article_audit import remove_work_dir, stray_result_files


class CompactAssemblyTests(unittest.TestCase):
    def setUp(self):
        texts = [('Lid guide', 'heading'), ('The lid closes within 7 days.', 'content'),
                 ('Ready to start?', 'content')]
        self.prepared = dict(
            article_id='ART-001', article_version='v1', article_sha256='a', knowledge_sha256=[],
            knowledge_files=['D:/k/30.md'],
            article_units=[dict(unit_id=f'U{n:03}', line=n, text=t, unit_type=k, candidate_evidence=[])
                           for n, (t, k) in enumerate(texts, 1)])
        self.prepared['article_units'][1]['candidate_evidence'] = [dict(chunk_id='K0001')]
        self.pass_payload = dict(
            coverage_review=dict(unit_classifications=[
                dict(unit_id='U002', classification='factual', reason='包含关于闭合时间的可核验断言。'),
                dict(unit_id='U003', classification='non_factual', category='question', reason='只提出问题，不陈述任何事实。'),
            ]),
            claim_inventory=[dict(unit_id='U002', article_quote='The lid closes within 7 days.',
                                  claim='The lid closes within 7 days.')])

    def frozen(self):
        return freeze_inventories(self.prepared, copy.deepcopy(self.pass_payload), copy.deepcopy(self.pass_payload))

    def test_normalize_fills_structural_rows_and_inventory_ids(self):
        result = normalize_inventory(self.prepared, self.pass_payload)
        rows = result['coverage_review']['unit_classifications']
        self.assertEqual([r['unit_id'] for r in rows], ['U001', 'U002', 'U003'])
        self.assertEqual(rows[0]['category'], 'heading')
        self.assertEqual(result['claim_inventory'][0]['inventory_id'], 'I001')
        self.assertNotIn('inventory_id', self.pass_payload['claim_inventory'][0])  # input untouched

    def test_claim_defaults_to_quote_and_claimed_unit_is_factual(self):
        payload = copy.deepcopy(self.pass_payload)
        payload['coverage_review']['unit_classifications'].pop(0)  # U002 row omitted
        payload['claim_inventory'] = [dict(unit_id='U002', article_quote='The lid closes **within 7 days**.')]
        result = normalize_inventory(self.prepared, payload)
        self.assertEqual(result['claim_inventory'][0]['claim'], 'The lid closes within 7 days.')
        whole = normalize_inventory(self.prepared, dict(payload, claim_inventory=[dict(unit_id='U002')]))
        self.assertEqual(whole['claim_inventory'][0]['article_quote'], 'The lid closes within 7 days.')
        self.assertEqual(whole['claim_inventory'][0]['claim'], 'The lid closes within 7 days.')
        row = result['coverage_review']['unit_classifications'][1]
        self.assertEqual((row['unit_id'], row['classification']), ('U002', 'factual'))
        self.assertGreaterEqual(len(row['reason']), 12)

    def test_assemble_judgments_copies_identity_and_requires_every_claim_once(self):
        frozen = self.frozen()
        verdict = dict(claim_id='C001', semantic_status='entailed', reason='证据正文直接陈述盖子在七天内闭合。',
                       evidence=[dict(quote='closes within 7 days')])
        judgments = assemble_judgments(self.prepared, frozen, self.pass_payload, [[verdict]], self.pass_payload)
        claim = judgments['claims'][0]
        self.assertEqual((claim['unit_id'], claim['article_line'], claim['claim']),
                         ('U002', 2, 'The lid closes within 7 days.'))
        self.assertEqual((claim['deepeval_verdict'], claim['verdict']), ('yes', 'supported'))
        self.assertEqual(claim['evidence'][0]['source_file'], 'D:/k/30.md')
        coverage = judgments['coverage_review']
        self.assertEqual((coverage['reviewed_unit_count'], coverage['factual_unit_count']), (3, 1))
        self.assertEqual(judgments['review_policy'], 'decisive_recheck_v1')
        with self.assertRaisesRegex(ValueError, 'missing'):
            assemble_judgments(self.prepared, frozen, self.pass_payload, [[]], self.pass_payload)
        for bad, message in ((dict(verdict, reason='太短'), '12'),
                             (dict(verdict, gap='七天闭合时限'), "uses 'support'"),
                             (dict(verdict, support='七天内闭合'), 'not both')):
            with self.assertRaisesRegex(ValueError, message):
                assemble_judgments(self.prepared, frozen, self.pass_payload, [[bad]], self.pass_payload)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            assemble_judgments(self.prepared, frozen, self.pass_payload, [[verdict], [verdict]], self.pass_payload)

    def test_stamped_reasons_are_rejected(self):
        texts = [(f'Heading {n}', 'heading') if n % 2 else (f'The lid closes within {n} days.', 'content')
                 for n in range(1, 11)]
        prepared = dict(self.prepared, article_units=[
            dict(unit_id=f'U{n:03}', line=n, text=t, unit_type=k, candidate_evidence=[])
            for n, (t, k) in enumerate(texts, 1)])
        content = [u for u in prepared['article_units'] if u['unit_type'] == 'content']
        first = dict(coverage_review=dict(unit_classifications=[]),
                     claim_inventory=[dict(unit_id=u['unit_id'], article_quote=u['text']) for u in content])
        blind = copy.deepcopy(first)
        blind['claim_inventory'].pop()  # a genuinely separate reading
        blind['coverage_review']['unit_classifications'].append(dict(
            unit_id=content[-1]['unit_id'], classification='non_factual', category='transition',
            reason='第二轮认为只是过渡语句。'))
        frozen = freeze_inventories(prepared, first, blind)
        stamped = [dict(claim_id=c['claim_id'], semantic_status='unknown', gap='允许知识附件未确认该完整主张。', evidence=[])
                   for c in frozen['claims']]
        with self.assertRaisesRegex(ValueError, 'stamped on more than 3'):
            assemble_judgments(prepared, frozen, first, [stamped], blind)
        own = [dict(row, gap=f'第{n}天闭合的时限') for n, row in enumerate(stamped, 1)]
        self.assertEqual(len(assemble_judgments(prepared, frozen, first, [own], blind)['claims']), 5)

    def test_identical_passes_are_not_independent(self):
        units = [dict(unit_id=f'U{n:03}', line=n, text=f'The lid closes within {n} days.', unit_type='content',
                      candidate_evidence=[]) for n in range(1, 6)]
        prepared = dict(self.prepared, article_units=units)
        same = dict(coverage_review=dict(unit_classifications=[]),
                    claim_inventory=[dict(unit_id=u['unit_id'], article_quote=u['text']) for u in units])
        with self.assertRaisesRegex(ValueError, 'not independent'):
            freeze_inventories(prepared, copy.deepcopy(same), copy.deepcopy(same))

    def test_passes_that_pick_different_propositions_in_most_units_do_not_freeze(self):
        # Rewording alone pairs at freeze; here each pass extracts a different proposition.
        units = [dict(unit_id=f'U{n:03}', line=n, text=f'The lid closes within {n} days and the box ships flat.',
                      unit_type='content', candidate_evidence=[]) for n in range(1, 6)]
        prepared = dict(self.prepared, article_units=units)
        first = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=[
            dict(unit_id=u['unit_id'], article_quote=u['text'], claim='The box ships flat.') for u in units])
        blind = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=[
            dict(unit_id=u['unit_id'], article_quote=u['text'], claim=f'The lid closes within {n} days.')
            for n, u in enumerate(units, 1)])
        with self.assertRaisesRegex(ValueError, 'split 5 of 5'):
            freeze_inventories(prepared, copy.deepcopy(first), copy.deepcopy(blind))
        merged = [dict(source_ref=f'first:I00{n}', replacement_refs=[f'blind:I00{n}'], all_predicates_preserved=True,
                       coverage_reason='同一闭合时限的同义改写，对象、谓词和数量完全一致。') for n in (1, 2)]
        frozen = freeze_inventories(prepared, copy.deepcopy(first), copy.deepcopy(blind), merged)
        self.assertEqual(len(frozen['claims']), 8)  # three units still differ: at the limit, not over it

    def divergent_passes(self, units: int, divergent: int):
        rows = [dict(unit_id=f'U{n:03}', line=n, text=f'The lid closes within {n} days.', unit_type='content',
                     candidate_evidence=[]) for n in range(1, units + 1)]
        prepared = dict(self.prepared, article_units=rows)
        blind = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=[
            dict(unit_id=u['unit_id'], article_quote=u['text']) for u in rows])
        first = copy.deepcopy(blind)
        for item in first['claim_inventory'][:divergent]:
            item['claim'] = 'Within that many days the lid closes.'
        return prepared, first, blind

    def test_divergence_limit_boundaries(self):
        for units, allowed in ((4, 3), (30, 6)):  # max(3, claimed units // 5)
            prepared, first, blind = self.divergent_passes(units, allowed)
            self.assertEqual(len(freeze_inventories(prepared, first, blind)['claims']), units + allowed)
            prepared, first, blind = self.divergent_passes(units, allowed + 1)
            with self.assertRaisesRegex(ValueError, f'split {allowed + 1} of {units}'):
                freeze_inventories(prepared, first, blind)

    def test_omitted_review_policy_means_the_current_default(self):
        first = dict(copy.deepcopy(self.pass_payload), review_policy='decisive_recheck_v1')
        self.assertEqual(self.frozen_with(first)['review_policy'], 'decisive_recheck_v1')
        for historical in ('two_pass_consensus', 'risk_weighted_v1'):
            # A historical policy is never inferred: the other pass must declare it too.
            with self.assertRaisesRegex(ValueError, 'same review_policy'):
                self.frozen_with(dict(first, review_policy=historical))

    def frozen_with(self, first):
        return freeze_inventories(self.prepared, first, copy.deepcopy(self.pass_payload))

    def test_status_flags_inputs_that_changed_after_prepare(self):
        with tempfile.TemporaryDirectory() as temp:
            knowledge = Path(temp) / '30.md'
            knowledge.write_text('closes within 7 days', encoding='utf-8')
            prepared = dict(self.prepared, knowledge_files=[str(knowledge)],
                            knowledge_sha256=[sha256_file(knowledge)], article_file='')
            frozen = Path(temp) / 'frozen.json'
            frozen.write_text(json.dumps(dict(article_sha256='a', knowledge_sha256=['old'])), encoding='utf-8')
            load = lambda path: json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(snapshot_conflicts(prepared, [frozen], load), ['frozen.json: 来自另一套输入'])
            knowledge.write_text('closes within 9 days', encoding='utf-8')
            self.assertTrue(any(item.startswith('30.md:') for item in snapshot_conflicts(prepared, [], load)))

    def test_evidence_from_another_article_is_rejected(self):
        frozen = self.frozen()
        verdict = dict(claim_id='C001', semantic_status='entailed', support='七天内闭合的同一时限',
                       evidence=[dict(source_file='D:/k/30.md', quote='closes within 7 days')])
        assemble_judgments(self.prepared, frozen, self.pass_payload, [[verdict]], self.pass_payload)
        foreign = dict(verdict, evidence=[dict(source_file='D:/other-article/30.md', quote='closes within 7 days')])
        with self.assertRaisesRegex(ValueError, 'outside this article'):
            assemble_judgments(self.prepared, frozen, self.pass_payload, [[foreign]], self.pass_payload)

    def test_evidence_quote_must_be_in_the_cited_file(self):
        with tempfile.TemporaryDirectory() as temp:
            knowledge = Path(temp) / '30.md'
            knowledge.write_bytes('> The lid closes within 7 days.\r\n> Ships in 3 weeks.\r\n'.encode('utf-8'))
            prepared = dict(self.prepared, knowledge_files=[str(knowledge)])
            frozen = freeze_inventories(prepared, copy.deepcopy(self.pass_payload), copy.deepcopy(self.pass_payload))
            verdict = dict(claim_id='C001', semantic_status='entailed', support='七天内闭合的同一时限',
                           evidence=[dict(quote='closes within 7 days.\n> Ships')])  # spans lines like check reads them
            assemble_judgments(prepared, frozen, self.pass_payload, [[verdict]], self.pass_payload)
            borrowed = dict(verdict, evidence=[dict(quote='The insert holds four rings.')])
            with self.assertRaisesRegex(ValueError, 'not in 30.md'):
                assemble_judgments(prepared, frozen, self.pass_payload, [[borrowed]], self.pass_payload)

    def test_managed_prepare_reads_only_the_knowledge_base_task_files(self):
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp) / 'P' / '04_文章任务' / '30_等待Faithfulness' / 'ART-001_Test'
            copy_dir = Path(temp) / 'bench' / 'inputs' / '001'
            for folder in (task, copy_dir):
                folder.mkdir(parents=True)
                (folder / '40_最终文章.md').write_text('# A', encoding='utf-8')
                (folder / '30_本篇知识库资料.md').write_text('# K', encoding='utf-8')
            material = task / '30_本篇知识库资料.md'
            self.assertEqual(managed_task_problem(task / '40_最终文章.md', material), '')
            self.assertIn('own task files', managed_task_problem(
                copy_dir / '40_最终文章.md', copy_dir / '30_本篇知识库资料.md'))
            (task / 'writing_ready.json').write_text(json.dumps({'knowledge_sha256': sha256_file(material)}), encoding='utf-8')
            self.assertEqual(managed_task_problem(task / '40_最终文章.md', material), '')
            material.write_text('# K edited', encoding='utf-8')
            self.assertIn('input_snapshot_changed', managed_task_problem(task / '40_最终文章.md', material))

    def test_line_ending_only_difference_is_restored_to_the_permitted_bytes(self):
        from prepare_article_audit import RESTORED_NOTE, managed_task_check

        crlf_text = '# K\r\n- 文章ID：ART-001\r\n> The lid closes within 7 days.\r\n'.encode('utf-8')
        lf_text = crlf_text.replace(b'\r\n', b'\n')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'P' / '04_文章任务'
            task = root / '30_等待Faithfulness' / 'ART-001_Test'
            task.mkdir(parents=True)
            article, material = task / '40_最终文章.md', task / '30_本篇知识库资料.md'
            article.write_text('# A', encoding='utf-8')
            permit = task / 'writing_ready.json'

            def bind(content: bytes, **extra) -> None:
                # The permit names the task as it was when issued, before it moved to 30_等待Faithfulness.
                record = dict(article_id='ART-001', article_version='v4', knowledge_sha256=hashlib.sha256(content).hexdigest(),
                              knowledge_file=str(root / '20_等待终稿' / task.name / material.name))
                record.update(extra)
                permit.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')

            def check(**ids):
                return managed_task_check(article, material, **ids)

            # LF now, CRLF permitted (P-005-ART-20261022-001) and the reverse: restored, text unchanged.
            for permitted, now in ((crlf_text, lf_text), (lf_text, crlf_text)):
                bind(permitted)
                material.write_bytes(now)
                self.assertEqual(check(article_id='ART-001', article_version='v4'), ('', RESTORED_NOTE))
                self.assertEqual(material.read_bytes(), permitted)
                self.assertEqual(check(), ('', ''))  # already the permitted bytes: nothing to do
            # Mixed endings normalise to the permitted CRLF file.
            bind(crlf_text)
            material.write_bytes(crlf_text.replace(b'\r\n', b'\n', 1))
            self.assertEqual(check(), ('', RESTORED_NOTE))
            self.assertEqual(material.read_bytes(), crlf_text)
            # Text, BOM or encoding changes are refused and the file is left exactly as found.
            for changed in (lf_text.replace(b'7 days', b'8 days'), b'\xef\xbb\xbf' + crlf_text,
                            crlf_text.decode('utf-8').encode('gb18030')):
                material.write_bytes(changed)
                problem, note = check()
                self.assertIn('more than line endings', problem)
                self.assertEqual((note, material.read_bytes()), ('', changed))
            # The permit's own file, when it still exists with the bound bytes, is the restore source.
            original = root / '20_等待终稿' / task.name / material.name
            original.parent.mkdir(parents=True)
            original.write_bytes(crlf_text)
            material.write_bytes(lf_text)
            self.assertEqual(check(), ('', RESTORED_NOTE))
            self.assertEqual(material.read_bytes(), crlf_text)
            # A permit that cannot be trusted refuses before anything is written.
            material.write_bytes(lf_text)
            for broken, message in (
                    (lambda: permit.write_text('{not json', encoding='utf-8'), '无法读取'),
                    (lambda: bind(crlf_text, knowledge_sha256='abc'), '有效的 knowledge_sha256'),
                    (lambda: bind(crlf_text, knowledge_file=str(root / '20_等待终稿' / 'ART-002_Other' / material.name)),
                     '不是本任务的30'),
                    (lambda: bind(crlf_text, article_id='ART-002'), 'article_id')):
                broken()
                problem, note = check(article_id='ART-001', article_version='v4')
                self.assertIn(message, problem)
                self.assertEqual((note, material.read_bytes()), ('', lf_text))
            bind(crlf_text)
            material.unlink()
            self.assertIn('没有 30_本篇知识库资料.md', check()[0])

    def test_compound_looking_claims_freeze_and_the_second_judgment_confirms_atomicity(self):
        texts = [('Lid guide', 'heading'), ('The tray holds rings, earrings and bracelets.', 'content')]
        prepared = dict(self.prepared, article_units=[
            dict(unit_id=f'U{n:03}', line=n, text=t, unit_type=k, candidate_evidence=[])
            for n, (t, k) in enumerate(texts, 1)])
        bare = dict(coverage_review=dict(unit_classifications=[]),
                    claim_inventory=[dict(unit_id='U002', article_quote=texts[1][0])])
        claim = freeze_inventories(prepared, copy.deepcopy(bare), copy.deepcopy(bare))['claims'][0]
        self.assertNotIn('atomicity_note', claim)  # boundaries are not policed at freeze
        claim.update(claim_id='C001', semantic_status='entailed')
        row = dict(atomicity_review=dict(status='atomic', independent_proposition_count=1,
                                         reason='托盘对三类首饰的同一容纳关系。'))
        self.assertNotIn('复合信号未说明为何仍属一个原子命题', validate_entailed_review(claim, row, set()))
        row['atomicity_review'] = dict(status='atomic', independent_proposition_count=2, reason='两个命题。')
        self.assertIn('复合信号未说明为何仍属一个原子命题', validate_entailed_review(claim, row, set()))

    def test_pass_of_comma_cut_fragments_is_redone(self):
        from audit_preflight import fragment_pass_problem

        words = ['size', 'For wholesale buyers', 'and color', '尺寸', '批发买家']
        cut = [dict(unit_id=f'U{n:03}', claim=f'Box {n} has a magnetic lid.') for n in range(1, 18)]
        cut += [dict(unit_id='U018', claim=w) for w in words[:3]]
        self.assertIn('3 of 20', fragment_pass_problem('blind', dict(claim_inventory=cut)))
        self.assertEqual(fragment_pass_problem('blind', dict(claim_inventory=cut[:-1])), '')  # 2 of 19: too few to judge
        whole_units = [dict(unit_id=f'U{n:03}') for n in range(1, 30)]  # compact rows naming whole units
        self.assertEqual(fragment_pass_problem('first', dict(claim_inventory=whole_units)), '')
        han = [dict(unit_id=f'U{n:03}', claim='盒子采用磁吸盖设计') for n in range(1, 18)]
        han += [dict(unit_id='U018', claim=w) for w in words[3:]]
        self.assertEqual(fragment_pass_problem('first', dict(claim_inventory=han)), '')  # 2 of 19
        han.append(dict(unit_id='U019', claim='尺寸与颜色'))
        self.assertIn('3 of 20', fragment_pass_problem('first', dict(claim_inventory=han)))

    def test_templated_reasons_that_only_swap_in_the_claim_are_stamped(self):
        from audit_compact import reject_stamped_reasons

        claims = {f'C{n:03}': f'The lid closes within {n} days.' for n in range(1, 6)}
        templated = {cid: f'允许证据未覆盖：本轮未能在允许知识附件中独立确认主张 {cid}：{text}'
                     for cid, text in claims.items()}
        with self.assertRaisesRegex(ValueError, 'stamped on more than 3'):
            reject_stamped_reasons(templated, 'primary verdicts', {cid: [text] for cid, text in claims.items()})
        reject_stamped_reasons(templated, 'primary verdicts')  # without identities every text differs
        restated = {cid: text for cid, text in claims.items()}
        with self.assertRaisesRegex(ValueError, 'stamped'):
            reject_stamped_reasons(restated, 'second judgments', {cid: [text] for cid, text in claims.items()})
        own = {cid: f'允许证据未覆盖：资料没有第{n}天闭合的时限' for n, cid in enumerate(claims, 1)}
        reject_stamped_reasons(own, 'primary verdicts', {cid: [text] for cid, text in claims.items()})

    def test_freeze_pairs_reworded_claims_but_not_negations_or_numbers(self):
        from audit_preflight import pair_score

        texts = [('For wholesale buyers, the goal is to record what the sample confirms.', 'content'),
                 ('The lid is magnetic.', 'content'), ('The lid closes within 7 days.', 'content'),
                 ('样品可以确认盒型结构和尺寸。', 'content')]
        prepared = dict(self.prepared, article_units=[
            dict(unit_id=f'U{n:03}', line=n, text=t, unit_type=k, candidate_evidence=[])
            for n, (t, k) in enumerate(texts, 1)])
        row = lambda unit, claim, note='': dict(unit_id=unit, article_quote=texts[int(unit[1:]) - 1][0], claim=claim,
                                                **({'derivation_note': note} if note else {}))
        first = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=[
            row('U001', 'A wholesale buyer should record what the sample confirms.', '把“目标是”改写为批发买家应做的动作。'),
            row('U002', 'The lid is magnetic.'), row('U003', 'The lid closes within 7 days.'),
            row('U004', '样品可以确认盒型结构和尺寸。')])
        blind = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=[
            row('U001', 'the goal is to record what the sample confirms'),
            row('U002', 'The lid is not magnetic.', '测试用：否定改写。'),
            row('U003', 'The lid closes within 10 days.', '测试用：数字改写。'),
            row('U004', '样品确认盒型结构和尺寸', '测试用：中文省略“可以”。')])
        frozen = freeze_inventories(prepared, first, blind)
        by_unit = {}
        for claim in frozen['claims']:
            by_unit.setdefault(claim['unit_id'], []).append(claim)
        self.assertEqual([c['claim'] for c in by_unit['U001']], ['A wholesale buyer should record what the sample confirms.'])
        self.assertEqual(by_unit['U001'][0]['inventory_origin'], 'both')
        self.assertEqual(len(by_unit['U002']), 2)  # "is" / "is not" never pair
        self.assertEqual(len(by_unit['U003']), 2)  # 7 days / 10 days never pair
        self.assertEqual([c['claim'] for c in by_unit['U004']], ['样品可以确认盒型结构和尺寸。'])
        pairs = [r for r in frozen['replacements'] if r.get('pairing') == 'content_word_overlap']
        self.assertEqual(sorted(r['source_ref'] for r in pairs), ['blind:I001', 'blind:I004'])
        self.assertEqual(frozen['source_mapping']['blind:I001'], ['first:I001'])
        # One-to-one: a compound kept whole pairs with at most one of the other pass's parts.
        self.assertGreaterEqual(pair_score('The box is rigid and the lid is magnetic.', 'The lid is magnetic.'), 0.75)
        self.assertEqual(pair_score('size', 'The box size is fixed.'), 0.0)  # too short to pair

    def test_coverage_review_replaces_the_second_blind_pass(self):
        from audit_preflight import second_pass_from_coverage

        texts = [('Lid guide', 'heading'), ('The lid closes within 7 days and the box ships flat.', 'content'),
                 ('Ready to start?', 'content'), ('This guide follows that path.', 'content')]
        prepared = dict(self.prepared, article_units=[
            dict(unit_id=f'U{n:03}', line=n, text=t, unit_type=k, candidate_evidence=[])
            for n, (t, k) in enumerate(texts, 1)])
        excluded = lambda unit, category, reason: dict(unit_id=unit, classification='non_factual',
                                                       category=category, reason=reason)
        first = dict(coverage_review=dict(unit_classifications=[
            excluded('U003', 'question', '只提出问题，不陈述任何事实。'),
            excluded('U004', 'transition', '只预告下文结构，没有可核验的关系。')]),
            claim_inventory=[dict(unit_id='U002', article_quote=texts[1][0], claim='The lid closes within 7 days.')])
        coverage = dict(coverage_review=dict(unit_classifications=[
            excluded('U003', 'question', '这是读者提问，没有断言关系。'),
            excluded('U004', 'transition', '这句只说明文章接下来的顺序。')]),
            claim_inventory=[dict(unit_id='U002', article_quote=texts[1][0], claim='The box ships flat.')])
        frozen = freeze_inventories(prepared, first, second_pass_from_coverage(prepared, first, coverage),
                                    coverage_review=True)
        origins = {c['claim']: c['inventory_origin'] for c in frozen['claims']}
        # The first pass's claims are confirmed, the reviewer's additions join, agreed exclusions stay out.
        self.assertEqual(origins, {'The lid closes within 7 days.': 'both', 'The box ships flat.': 'blind_only'})
        # Disagreeing with an exclusion means writing the unit's claims; the union keeps them.
        disagree = copy.deepcopy(coverage)
        disagree['coverage_review']['unit_classifications'].pop()
        disagree['claim_inventory'].append(dict(unit_id='U004', article_quote=texts[3][0],
                                                claim='This guide follows that path.'))
        frozen = freeze_inventories(prepared, first, second_pass_from_coverage(prepared, first, disagree),
                                    coverage_review=True)
        self.assertIn('U004', {c['unit_id'] for c in frozen['claims']})
        # Every first-pass exclusion needs the reviewer's verdict, and claimed units are not re-classified.
        silent = copy.deepcopy(coverage)
        silent['coverage_review']['unit_classifications'].pop()
        with self.assertRaisesRegex(ValueError, '缺少复查结论.*U004'):
            second_pass_from_coverage(prepared, first, silent)
        overreach = copy.deepcopy(coverage)
        overreach['coverage_review']['unit_classifications'].append(excluded('U002', 'transition', '这句只是过渡说明。'))
        with self.assertRaisesRegex(ValueError, 'U002：第一轮已有主张'):
            second_pass_from_coverage(prepared, first, overreach)
        # Claims plus an exclusion for the same unit in the review contradict each other.
        both = copy.deepcopy(coverage)
        both['claim_inventory'].append(dict(unit_id='U004', article_quote=texts[3][0], claim='This guide follows that path.'))
        with self.assertRaisesRegex(ValueError, 'claims and a non_factual row in the same pass.*blind: U004'):
            freeze_inventories(prepared, first, second_pass_from_coverage(prepared, first, both), coverage_review=True)

    def test_pairing_ignores_the_shared_frame_of_a_many_claim_unit(self):
        from audit_preflight import content_words

        # P-005-ART-20261022-001 U045: one sentence, two passes, many claims sharing "JERL ... process".
        text = ("JERL Packaging's four-step process is to communicate requirements and provide design drafts, "
                "quote and confirm, create prototypes for approval, and move to mass production and delivery "
                "after sample confirmation.")
        prepared = dict(self.prepared, article_units=[dict(unit_id='U001', line=1, text=text, unit_type='content',
                                                           candidate_evidence=[])])
        note = '把句中的一个步骤写成以公司流程为主语的完整命题。'
        rows = lambda claims: [dict(unit_id='U001', article_quote=text, claim=c, derivation_note=note) for c in claims]
        first = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=rows([
            "JERL Packaging's process includes providing design drafts.",
            "JERL Packaging's process includes quoting.",
            "JERL Packaging's process includes confirmation after quoting.",
            "JERL Packaging describes its process as four steps."]))
        blind = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=rows([
            "JERL Packaging provides design drafts in the first step of its four-step process.",
            "JERL Packaging quotes in the second step of its four-step process.",
            "JERL Packaging confirms in the second step of its four-step process."]))
        self.assertNotIn('s', content_words("JERL Packaging's process"))  # a possessive is not a word
        claims = [c['claim'] for c in freeze_inventories(prepared, first, blind)['claims']]
        # Same fact in two styles merges; the four-steps fact keeps its own claim instead of
        # pairing with a step claim on the shared words JERL/process/four/step.
        self.assertIn("JERL Packaging describes its process as four steps.", claims)
        self.assertNotIn("JERL Packaging's process includes quoting.", claims)
        self.assertIn("JERL Packaging quotes in the second step of its four-step process.", claims)
        self.assertNotIn("JERL Packaging's process includes providing design drafts.", claims)

    def test_paired_blind_rows_leave_the_review_inventory(self):
        texts = [('Lid guide', 'heading'), ('For wholesale buyers, the goal is to record what the sample confirms.', 'content')]
        prepared = dict(self.prepared, article_units=[
            dict(unit_id=f'U{n:03}', line=n, text=t, unit_type=k, candidate_evidence=[])
            for n, (t, k) in enumerate(texts, 1)])
        first = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=[dict(
            unit_id='U002', article_quote=texts[1][0], claim='A wholesale buyer should record what the sample confirms.',
            derivation_note='把“目标是”改写为批发买家应做的动作。')])
        blind = dict(coverage_review=dict(unit_classifications=[]), claim_inventory=[dict(
            unit_id='U002', article_quote='the goal is to record what the sample confirms')])
        frozen = freeze_inventories(prepared, first, blind)
        verdict = dict(claim_id='C001', semantic_status='unknown', gap='资料没有记录样品确认内容的要求', evidence=[])
        judgments = assemble_judgments(prepared, frozen, first, [[verdict]], blind)
        review = assemble_review(prepared, frozen, blind, recheck_list(judgments), [[]])
        self.assertEqual(review['claim_inventory'], [])  # covered by the kept first-pass wording

    def test_review_rows_need_exact_fragments(self):
        with tempfile.TemporaryDirectory() as temp:
            knowledge = Path(temp) / '30.md'
            knowledge.write_bytes(b'> The lid closes within 7 days.\n')
            prepared = dict(self.prepared, knowledge_files=[str(knowledge)])
            frozen = freeze_inventories(prepared, copy.deepcopy(self.pass_payload), copy.deepcopy(self.pass_payload))
            verdict = dict(claim_id='C001', semantic_status='entailed', support='七天内闭合的同一时限',
                           evidence=[dict(quote='closes within 7 days')])
            judgments = assemble_judgments(prepared, frozen, self.pass_payload, [[verdict]], self.pass_payload)
            good = dict(status='covered', claim_fragment='closes within 7 days',
                        evidence_fragment='closes within 7 days', reason='证据覆盖同一关系。')
            row = dict(claim_id='C001', independent_semantic_status='entailed',
                       challenge_reason='独立复核确认证据给出同一闭合时限。',
                       atomicity_review=dict(status='atomic', independent_proposition_count=1, reason='只断言一个闭合时限。'),
                       entailment_checks=dict(subject_object=good, predicate_relation=dict(good, claim_fragment='')))
            with self.assertRaisesRegex(ValueError, 'C001.predicate_relation.claim_fragment'):
                assemble_review(prepared, frozen, self.pass_payload, recheck_list(judgments), [[row]])
            row['entailment_checks']['predicate_relation'] = dict(good, evidence_fragment='closes in a week')
            with self.assertRaisesRegex(ValueError, 'C001.predicate_relation.evidence_fragment'):
                assemble_review(prepared, frozen, self.pass_payload, recheck_list(judgments), [[row]])

    def test_reset_archives_an_unrendered_attempt_only(self):
        from audit_preflight import reset_attempt

        with tempfile.TemporaryDirectory() as temp:
            result = Path(temp)
            (result / '_work').mkdir()
            for name in ('A-prepared.json', 'A-judgments.json', '_work/A-audit-view.md', '_work/frozen.json'):
                (result / name).write_text('{}', encoding='utf-8')
            outcome = reset_attempt(result)
            self.assertEqual(outcome['moved'], 2)
            self.assertIn('## 抽取轮写法', (result / '_work/A-audit-view.md').read_text(encoding='utf-8'))  # rebuilt, not the stale '{}'
            self.assertTrue((result / '_work/A-extract-view.md').is_file())
            self.assertTrue((result / '_work/A-evidence-view.md').is_file())
            self.assertEqual(sorted(p.name for p in result.iterdir()), ['A-prepared.json', '_work'])
            self.assertTrue((result / '_work/A-audit-view.md').is_file())
            self.assertTrue((result / '_work/attempt-1/frozen.json').is_file())
            # A rendered report nobody imported is archived too; the view comes back from prepared.
            (result / 'A-prepared.json').write_text(json.dumps(dict(self.prepared, knowledge_files=[])), encoding='utf-8')
            (result / 'faithfulness_summary.md').write_text('x', encoding='utf-8')
            (result / '_work/A-audit-view.md').unlink()
            self.assertEqual(reset_attempt(result)['moved'], 1)
            self.assertTrue((result / '_work/attempt-2/faithfulness_summary.md').is_file())
            self.assertTrue((result / '_work/A-audit-view.md').is_file())
            (result / 'article_completed.json').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '已导入'):
                reset_attempt(result)

    def test_judged_attempt_refuses_to_refreeze(self):
        import subprocess

        with tempfile.TemporaryDirectory() as temp:
            result = Path(temp)
            (result / '_work').mkdir()
            (result / 'A-prepared.json').write_text(json.dumps(self.prepared), encoding='utf-8')
            for name in ('first.json', 'blind.json'):
                (result / '_work' / name).write_text(json.dumps(self.pass_payload), encoding='utf-8')
            script = Path(__file__).resolve().parents[1] / 'scripts' / 'audit_preflight.py'
            freeze = [sys.executable, str(script), 'freeze', '--prepared', str(result / 'A-prepared.json'),
                      '--first-inventory', str(result / '_work/first.json'),
                      '--blind-inventory', str(result / '_work/blind.json'), '--output', str(result / '_work/frozen.json')]
            for _ in range(2):  # before judging, the same frozen file can be rewritten
                run = subprocess.run(freeze, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(run.returncode, 0, run.stderr)
            claims = (result / '_work/claims.md').read_text(encoding='utf-8')  # what the judges read
            self.assertIn('C001 [U002] The lid closes within 7 days.', claims)
            (result / 'A-judgments.json').write_text(json.dumps(dict(claims=[])), encoding='utf-8')
            # Patching a pass and freezing again is refused once judging has started.
            run = subprocess.run([sys.executable, str(script), 'freeze', '--prepared', str(result / 'A-prepared.json'),
                                  '--first-inventory', str(result / '_work/first.json'),
                                  '--blind-inventory', str(result / '_work/blind.json'),
                                  '--output', str(result / '_work/frozen-2.json')],
                                 capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(run.returncode, 0)
            self.assertIn('reset', run.stderr)

    def test_union_claim_turns_first_pass_exclusion_factual(self):
        first = copy.deepcopy(self.pass_payload)
        first['claim_inventory'] = []
        first['coverage_review']['unit_classifications'][0] = dict(
            unit_id='U002', classification='non_factual', category='transition', reason='第一轮认为只是过渡语句。')
        frozen = freeze_inventories(self.prepared, first, copy.deepcopy(self.pass_payload))
        verdict = dict(claim_id='C001', semantic_status='unknown', gap='盖子闭合所需的时间', evidence=[])
        judgments = assemble_judgments(self.prepared, frozen, first, [[verdict]], self.pass_payload)
        self.assertEqual(judgments['claims'][0]['reason'], '允许证据未覆盖：盖子闭合所需的时间')
        row = judgments['coverage_review']['unit_classifications'][1]
        self.assertEqual(row['classification'], 'factual')
        self.assertNotIn('category', row)

    def test_assemble_review_fills_only_checks_the_claim_does_not_need(self):
        frozen = self.frozen()
        verdict = dict(claim_id='C001', semantic_status='entailed', support='七天内闭合的同一时限',
                       evidence=[dict(quote='closes within 7 days')])
        judgments = assemble_judgments(self.prepared, frozen, self.pass_payload, [[verdict]], self.pass_payload)
        covered = dict(status='covered', claim_fragment='closes within 7 days',
                       evidence_fragment='closes within 7 days', reason='证据覆盖同一关系。')
        row = dict(claim_id='C001', independent_semantic_status='entailed', challenge_reason='独立复核确认证据给出同一闭合时限。',
                   atomicity_review=dict(status='atomic', independent_proposition_count=1, reason='只断言一个闭合时限。'),
                   entailment_checks=dict(subject_object=covered, predicate_relation=covered))
        self.assertEqual(required_checks('The lid closes within 7 days.'),
                         {'subject_object', 'predicate_relation', 'scope_condition', 'quantity_time_version'})
        # A required dimension is never auto-filled; leaving it out is a form gap reported before anything is written.
        with self.assertRaises(ValueError) as refused:
            assemble_review(self.prepared, frozen, self.pass_payload, recheck_list(judgments), [[row]])
        for gap in ('C001.scope_condition（主张措辞含此维度', 'C001.quantity_time_version（主张措辞含此维度',
                    'C001.challenge_reason（至少20字）', 'C001.atomicity_review.reason（至少12字）'):
            self.assertIn(gap, str(refused.exception))
        row['challenge_reason'] = '独立复核确认证据给出同一闭合时限，范围与数量都与主张一致。'
        row['atomicity_review']['reason'] = '只断言盖子在七天内闭合这一个时限。'
        row['entailment_checks'].update(
            scope_condition=dict(status='missing', claim_fragment='within', evidence_fragment='', reason='证据没有说明适用范围。'),
            quantity_time_version=dict(covered, reason='证据给出同一个7天时限。'))
        review = assemble_review(self.prepared, frozen, self.pass_payload, recheck_list(judgments), [[row]])
        checks = review['claim_reviews'][0]['entailment_checks']
        self.assertEqual(checks['causal_effect']['reason'], AUTO_NOT_APPLICABLE)  # not required: filled
        # The reviewer's own conclusion (scope missing) is not a form gap: the reconciler downgrades it.
        problems = validate_entailed_review(judgments['claims'][0], review['claim_reviews'][0], set())
        self.assertIn('scope_condition存在未覆盖信息', problems)
        self.assertEqual(review['claim_reviews'][0]['claim'], 'The lid closes within 7 days.')
        self.assertEqual(review['claim_inventory'][0]['matched_claim_id'], 'C001')
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            assemble_review(self.prepared, frozen, self.pass_payload, recheck_list(judgments),
                            [[row, dict(row, claim_id='C009')]])

    def test_review_fields_that_status_and_fragments_already_state_are_filled(self):
        frozen = self.frozen()
        verdict = dict(claim_id='C001', semantic_status='entailed', support='七天内闭合的同一时限',
                       evidence=[dict(quote='closes within 7 days')])
        judgments = assemble_judgments(self.prepared, frozen, self.pass_payload, [[verdict]], self.pass_payload)
        covered = lambda part: dict(status='covered', claim_fragment=part, evidence_fragment='closes within 7 days')
        # As Codex wrote them in P-005 v4: `count` for independent_proposition_count, no per-check reasons.
        row = dict(claim_id='C001', independent_semantic_status='entailed',
                   challenge_reason='独立复核确认证据给出同一闭合时限，范围与数量都与主张一致。',
                   atomicity_review=dict(status='atomic', count=1, reason='只断言盖子在七天内闭合这一个时限。'),
                   entailment_checks=dict(subject_object=covered('The lid'), predicate_relation=covered('closes'),
                                          scope_condition=covered('within 7 days'),
                                          quantity_time_version=covered('7 days')))
        review = assemble_review(self.prepared, frozen, self.pass_payload, recheck_list(judgments), [[row]])
        assembled = review['claim_reviews'][0]
        self.assertEqual(assembled['atomicity_review']['independent_proposition_count'], 1)
        self.assertIn('对应证据片段', assembled['entailment_checks']['subject_object']['reason'])
        self.assertEqual(validate_entailed_review(judgments['claims'][0], assembled, set()), [])  # stays supported
        contradictory = copy.deepcopy(row)
        contradictory['atomicity_review'] = dict(status='atomic', independent_proposition_count=2,
                                                 reason='只断言盖子在七天内闭合这一个时限。')
        with self.assertRaisesRegex(ValueError, 'atomic 只能对应1个命题'):
            assemble_review(self.prepared, frozen, self.pass_payload, recheck_list(judgments), [[contradictory]])

    def test_check_lists_missing_fields_but_not_the_reviewers_conclusions(self):
        from audit_preflight import is_review_outcome

        for outcome in ('第二轮认为主张仍包含多个独立命题', '复合信号未说明为何仍属一个原子命题',
                        'scope_condition未被证据覆盖', 'causal_effect存在未覆盖信息', '第二轮理由为重复模板'):
            self.assertTrue(is_review_outcome(outcome), outcome)
        for gap in ('causal_effect检查缺失', 'subject_object缺少具体核对说明', '第二轮理由过短', '缺少原子性复核'):
            self.assertFalse(is_review_outcome(gap), gap)

    def test_assembled_records_can_be_rebuilt_until_reconciled(self):
        from audit_preflight import refuse_final_record, supersede_assembled

        with tempfile.TemporaryDirectory() as temp:
            result = Path(temp)
            (result / '_work').mkdir()
            judgments = result / 'A-judgments.json'
            for path in (judgments, result / 'A-adversarial-review.json', result / '_work/recheck.json',
                         result / '_work/receipt.json', result / '_work/verdicts-1.json'):
                path.write_text('{}', encoding='utf-8')
            refuse_final_record(judgments)  # not reconciled: no error
            target = Path(supersede_assembled(judgments, 'assemble-judgments'))
            # The records built from the old verdicts go with them; the compact shards stay to be corrected.
            self.assertEqual(sorted(p.name for p in target.iterdir()),
                             ['A-adversarial-review.json', 'A-judgments.json', 'receipt.json', 'recheck.json'])
            self.assertTrue((result / '_work/verdicts-1.json').is_file())
            judgments.write_text(json.dumps(dict(reconciliation=dict(completed=True))), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'already reconciled'):
                refuse_final_record(judgments)

    def test_managed_coverage_rules_fail_at_freeze(self):
        managed = dict(self.prepared, integration_mode='manage-article-knowledge-v0.6')
        bad = copy.deepcopy(self.pass_payload)
        bad['coverage_review']['unit_classifications'][1]['category'] = 'transition'
        self.assertEqual(coverage_problems(managed, self.pass_payload), [])
        self.assertIn('U003: 以问号结尾的单元必须用 question 类别', coverage_problems(managed, bad))
        with self.assertRaisesRegex(ValueError, 'renderer would reject'):
            freeze_inventories(managed, bad, copy.deepcopy(self.pass_payload))
        invented = copy.deepcopy(self.pass_payload)
        invented['coverage_review']['unit_classifications'][1]['category'] = 'discourse_framing'
        problem = coverage_problems(managed, invented)[0]
        self.assertIn("'discourse_framing'", problem)
        self.assertIn('transition/cta/subjective', problem)  # the error names what is allowed

    def test_view_lists_units_once_with_structural_marker(self):
        prepared = dict(self.prepared, knowledge_files=[])
        view = build_view(prepared)
        self.assertIn('- U001 L1 [heading·结构排除]', view)
        self.assertIn('候选:K0001', view)
        # An extraction subagent may read only the view: it carries the pass format and every allowed category.
        from render_article_audit import NON_FACTUAL_CATEGORIES
        self.assertIn('## 抽取轮写法', view)
        for category in NON_FACTUAL_CATEGORIES:
            self.assertIn(f'`{category}`', view)
        from audit_compact import view_parts
        from reconcile_article_audit import NONCLAIM_CATEGORIES
        self.assertEqual(NONCLAIM_CATEGORIES, NON_FACTUAL_CATEGORIES)
        parts = view_parts(prepared)
        self.assertEqual(parts['-audit-view.md'], view)
        extract, evidence = parts['-extract-view.md'], parts['-evidence-view.md']
        # Extraction reads units and the guide but no evidence; judges read evidence without the guide.
        self.assertIn('## 抽取轮写法', extract)
        self.assertIn('- U001 L1 [heading·结构排除]', extract)
        self.assertNotIn('## 允许证据正文', extract)
        self.assertIn('## 允许证据正文', evidence)
        self.assertNotIn('## 抽取轮写法', evidence)
        self.assertNotIn('## 文章单元', evidence)


class WorkDirTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ('A-prepared.json', 'A-judgments.json', 'faithfulness_summary.md', 'unsupported-disposition.json'):
            (self.root / name).write_text('{}', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_work_dir_is_removed_and_strays_reported(self):
        (self.root / '_work').mkdir()
        (self.root / '_work' / 'frozen.json').write_text('{}', encoding='utf-8')
        (self.root / 'build_first.py').write_text('', encoding='utf-8')
        self.assertEqual(remove_work_dir(self.root), (True, ''))
        self.assertFalse((self.root / '_work').exists())
        self.assertEqual(stray_result_files(self.root), ['build_first.py'])
        self.assertEqual(remove_work_dir(self.root), (False, ''))  # nothing to remove

    def test_linked_work_dir_is_never_followed(self):
        target = self.root / 'elsewhere'
        target.mkdir()
        (target / 'keep.txt').write_text('x', encoding='utf-8')
        try:
            (self.root / '_work').symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        removed, problem = remove_work_dir(self.root)
        self.assertFalse(removed)
        self.assertIn('link', problem)
        self.assertTrue((target / 'keep.txt').exists())


if __name__ == '__main__':
    unittest.main()
