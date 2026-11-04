import unittest

from scripts.arabic_dialogue_intelligence import analyze_dialogue, analyze_segments


class ArabicDialogueIntelligenceTests(unittest.TestCase):
    def test_detects_question_and_maghrebi_challenge(self):
        result = analyze_dialogue("علاش حدث هذا؟ بصح هذا غير صحيح")
        labels = {cue["type"] for cue in result["cues"]}
        self.assertIn("question", labels)
        self.assertIn("challenge", labels)

    def test_detects_revelation_and_conclusion(self):
        result = analyze_dialogue("المفاجأة أن لدينا وثيقة. الخلاصة أن القرار تغير.")
        labels = {cue["type"] for cue in result["cues"]}
        self.assertIn("revelation", labels)
        self.assertIn("conclusion", labels)
        self.assertEqual(result["ending_completeness_proxy"], "strong_signal")

    def test_evidence_is_returned(self):
        result = analyze_dialogue("لماذا؟")
        question = next(c for c in result["cues"] if c["type"] == "question")
        self.assertEqual(question["evidence"], ["لماذا"])

    def test_segment_analysis_does_not_mutate_input(self):
        source = [{"text": "الخلاصة واضحة.", "score": 80}]
        result = analyze_segments(source)
        self.assertNotIn("dialogue_intelligence", source[0])
        self.assertIn("dialogue_intelligence", result[0])
        self.assertEqual(result[0]["score"], 80)

    def test_invalid_types_rejected(self):
        with self.assertRaises(TypeError):
            analyze_dialogue(None)
        with self.assertRaises(TypeError):
            analyze_segments({})


if __name__ == "__main__":
    unittest.main()
