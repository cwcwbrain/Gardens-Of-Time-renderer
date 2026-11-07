"""Geometry checks for local numbered badges, without scene/network fixtures."""
import copy
import math
import unittest

from gardens.pointer_layout import place_numbered_pointers, validate_pointer_layout


def marker(number, anchor, badge_size=(32, 26), object_id=None):
    return {
        'number': number,
        'object_id': number if object_id is None else object_id,
        'anchor': list(anchor),
        'badge_size': list(badge_size),
    }


def drawn_marker(number, box, start, tip):
    """Specify independent public geometry for intentionally invalid examples."""
    dx, dy = tip[0] - start[0], tip[1] - start[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    result = marker(number, tip, (box[2] - box[0], box[3] - box[1]))
    result.update({
        'number_box': list(box),
        'pointer_start': list(start),
        'pointer_tip': list(tip),
        'pointer_route': [list(start), list(tip)],
        'pointer_arrow': [
            list(tip),
            [tip[0] - 7 * ux + 3 * uy, tip[1] - 7 * uy - 3 * ux],
            [tip[0] - 7 * ux - 3 * uy, tip[1] - 7 * uy + 3 * ux],
        ],
        'pointer_length': length,
    })
    return result


class PointerLayoutTests(unittest.TestCase):
    def assert_layout(self, markers, scene=(20, 30, 1300, 788), canvas=(1800, 900)):
        before = {m['object_id']: list(m['anchor']) for m in markers}
        result = place_numbered_pointers(markers, scene)
        self.assertIs(result, markers)
        self.assertEqual(validate_pointer_layout(markers, canvas, scene), [])
        for item in markers:
            self.assertEqual(item['anchor'], before[item['object_id']])
            self.assertEqual(item['pointer_tip'], [
                item['anchor'][0] + scene[0], item['anchor'][1] + scene[1],
            ])
            self.assertEqual(item['pointer_route'], [item['pointer_start'], item['pointer_tip']])
            self.assertGreaterEqual(item['pointer_length'], 12)
            self.assertLessEqual(item['pointer_length'], 96)
            left, top, right, bottom = item['number_box']
            self.assertGreaterEqual(left, scene[0])
            self.assertGreaterEqual(top, scene[1])
            self.assertLess(right, scene[2])
            self.assertLess(bottom, scene[3])
        return result

    def test_sixty_eight_targets_have_local_routes_and_fixed_anchors(self):
        # Scene-scale coverage plus a dense middle patch, independent of ignored
        # output/ files produced during manual real-bundle validation.
        items = [marker(i + 1, (60 + (i % 10) * 121, 55 + (i // 10) * 105))
                 for i in range(68)]
        self.assert_layout(items)

    def test_close_sixteen_pixel_pair_and_edge_targets(self):
        anchors = [(250, 250), (266, 250), (292, 265), (275, 295),
                   (0, 0), (639, 0), (639, 399), (0, 399),
                   (0, 200), (639, 200), (320, 0), (320, 399)]
        items = [marker(i + 1, anchor) for i, anchor in enumerate(anchors)]
        self.assert_layout(items, (25, 35, 665, 435), (900, 600))

    def test_three_digit_numbers_keep_measured_badge_dimensions(self):
        items = [marker(100 + i, (80 + 110 * i, 100), (40.25, 26)) for i in range(5)]
        self.assert_layout(items, (10, 10, 650, 300), (900, 400))
        for item in items:
            box = item['number_box']
            self.assertEqual((box[2] - box[0], box[3] - box[1]), (41, 26))

    def test_repeat_and_reordered_input_are_deterministic(self):
        original = [marker(i + 1, (70 + (i % 7) * 85, 60 + (i // 7) * 85))
                    for i in range(28)]
        first, repeated, reordered = copy.deepcopy(original), copy.deepcopy(original), copy.deepcopy(original[::-1])
        scene = (30, 40, 700, 450)
        for items in (first, repeated, reordered):
            place_numbered_pointers(items, scene)
        as_map = lambda items: {m['object_id']: m for m in items}
        self.assertEqual(as_map(first), as_map(repeated))
        self.assertEqual(as_map(first), as_map(reordered))

    def test_coincident_and_unseparable_strokes_fail_without_mutation(self):
        for second in ((100, 100), (105, 100)):
            with self.subTest(second=second):
                items = [marker(1, (100, 100), object_id='target-a'),
                         marker(2, second, object_id='target-b')]
                before = copy.deepcopy(items)
                with self.assertRaisesRegex(ValueError, 'target-a.*target-b'):
                    place_numbered_pointers(items, (0, 0, 300, 300))
                self.assertEqual(items, before)

    def test_impossible_badge_or_offscene_anchor_fails_without_mutation(self):
        for item, scene in ((marker(1, (20, 20), (80, 26)), (0, 0, 40, 40)),
                            (marker(1, (40, 10)), (0, 0, 40, 40))):
            with self.subTest(item=item):
                items = [item]
                before = copy.deepcopy(items)
                with self.assertRaises(ValueError):
                    place_numbered_pointers(items, scene)
                self.assertEqual(items, before)

    def test_validator_rejects_crossing_routes(self):
        items = [drawn_marker(1, (10, 35, 30, 55), (30, 45), (90, 85)),
                 drawn_marker(2, (90, 35, 110, 55), (90, 45), (30, 85))]
        issues = validate_pointer_layout(items, (150, 150), (0, 0, 150, 150))
        self.assertTrue(any('pointer strokes intersect' in issue for issue in issues), issues)

    def test_validator_rejects_touching_strokes_without_centerline_crossing(self):
        # The badges are separated and the actual lines are parallel. Outlined
        # leader strokes still collide in the three-pixel gap between them.
        items = [drawn_marker(1, (10, 10, 30, 30), (30, 20), (100, 20)),
                 drawn_marker(2, (110, 13, 130, 33), (110, 23), (40, 23))]
        issues = validate_pointer_layout(items, (160, 100), (0, 0, 160, 100))
        self.assertTrue(any('pointer strokes intersect or touch' in issue for issue in issues), issues)

    def test_validator_checks_badges_and_leaders_through_other_badges(self):
        overlap = [drawn_marker(1, (10, 30, 40, 50), (40, 40), (80, 40)),
                   drawn_marker(2, (20, 35, 50, 55), (50, 45), (90, 45))]
        issues = validate_pointer_layout(overlap, (180, 130), (0, 0, 180, 130))
        self.assertTrue(any('number badges overlap' in issue for issue in issues), issues)
        through = [drawn_marker(1, (10, 30, 30, 50), (30, 40), (110, 40)),
                   drawn_marker(2, (60, 30, 80, 50), (70, 50), (70, 100))]
        issues = validate_pointer_layout(through, (180, 130), (0, 0, 180, 130))
        self.assertTrue(any('stroke intersects another number badge' in issue for issue in issues), issues)

    def test_validator_checks_arrowhead_beyond_centerline(self):
        # The horizontal leader ends seven pixels before the vertical leader;
        # its arrow flank still enters the vertical arrow's outline allowance.
        items = [drawn_marker(1, (10, 40, 30, 60), (30, 50), (80, 50)),
                 drawn_marker(2, (63, 90, 83, 110), (73, 90), (73, 57))]
        issues = validate_pointer_layout(items, (150, 150), (0, 0, 150, 150))
        self.assertTrue(any('arrowhead' in issue for issue in issues), issues)

    def test_validator_rejects_moved_anchor_malformed_route_and_wrong_arrow(self):
        items = [marker(1, (120, 120))]
        place_numbered_pointers(items, (20, 30, 420, 330))
        cases = []
        moved = copy.deepcopy(items)
        moved[0]['anchor'][0] += 1
        cases.append((moved, 'actual visible anchor'))
        route = copy.deepcopy(items)
        route[0]['pointer_route'].append([0, 0])
        cases.append((route, 'Malformed pointer route'))
        arrow = copy.deepcopy(items)
        arrow[0]['pointer_arrow'][1][0] += 1
        cases.append((arrow, 'Arrowhead does not follow'))
        for changed, text in cases:
            with self.subTest(text=text):
                issues = validate_pointer_layout(changed, (600, 400), (20, 30, 420, 330))
                self.assertTrue(any(text in issue for issue in issues), issues)

    def test_validator_rejects_badge_outside_scene_and_wrong_recorded_size(self):
        outside = drawn_marker(1, (-5, 40, 15, 60), (15, 50), (70, 50))
        issues = validate_pointer_layout([outside], (150, 150), (0, 0, 150, 150))
        self.assertTrue(any('outside the scene' in issue for issue in issues), issues)
        outside['number_box'] = [5, 40, 25, 60]
        outside['badge_size'] = [21, 20]
        outside['pointer_start'] = [25, 50]
        outside['pointer_route'][0] = [25, 50]
        outside['pointer_length'] = 45
        issues = validate_pointer_layout([outside], (150, 150), (0, 0, 150, 150))
        self.assertTrue(any('Number badge dimensions changed' in issue for issue in issues), issues)

    def test_empty_scene_markers_are_valid(self):
        items = []
        self.assertIs(place_numbered_pointers(items, (10, 10, 200, 150)), items)
        self.assertEqual(validate_pointer_layout(items, (300, 200), (10, 10, 200, 150)), [])


if __name__ == '__main__':
    unittest.main()
