"""Contract checks for exact, adaptive CPU news highlights."""
import html
import unittest
from transforms.post_normalization.summarizer import Summarizer, sentence_split, normalize


class NewsHighlightTests(unittest.TestCase):
    def check_source(self, body, result):
        self.assertEqual(result.summary, " ".join(result.selected_sentences))
        self.assertEqual(result.selected_indices, sorted(set(result.selected_indices)))
        for sentence, (a, b) in zip(result.selected_sentences, result.source_spans):
            self.assertIn(sentence, normalize(html.unescape(body)))
            self.assertEqual(sentence, normalize(result.cleaned_text[a:b]))

    def test_abbreviations_and_amounts_remain_intact(self):
        body = "Dr. Maria A. Cruz and Atty. Jose B. Santos inspected Alpha Inc. The loss was approx. Php 3,500.00. Ma. Cristina Roque announced the findings."
        parts = sentence_split(body)
        self.assertEqual(len(parts), 3)
        self.assertIn("Dr. Maria A. Cruz", parts[0])
        self.assertIn("approx. Php 3,500.00.", parts[1])
        self.assertTrue(parts[2].startswith("Ma. Cristina"))

    def test_final_outcome_kept_with_main_event(self):
        body = "The police arrested Daniel Brown on suspicion of fraud. Investigators examined documents and interviewed several witnesses. Brown initially pleaded not guilty to the charges. The court later acquitted Daniel Brown after reviewing the evidence. Officials thanked the investigators and praised their unwavering commitment."
        result = Summarizer().summarize(body)
        self.check_source(body, result)
        self.assertIn("arrested Daniel Brown", result.summary)
        self.assertIn("acquitted Daniel Brown", result.summary)
        self.assertNotIn("thanked", result.summary)

    def test_registered_company_not_authorized_to_solicit(self):
        body = "Alpha Corporation is a company registered with the Commission. Alpha Corporation is not authorized to solicit investments from the public. Investors are advised not to invest in the scheme. For further details contact us at media@example.com."
        result = Summarizer().summarize(body)
        self.check_source(body, result)
        self.assertIn("not authorized", result.summary)
        self.assertNotIn("media@example.com", result.summary)

    def test_adaptive_count_and_order(self):
        small = "The agency launched a training programme for local officials. The programme will cover budgeting and public service."
        large = " ".join(f"The agency announced programme {i} with new training objectives and additional support for officials in district {i}." for i in range(45))
        a, b = Summarizer().summarize(small), Summarizer().summarize(large)
        self.assertGreater(b.target, a.target)
        self.check_source(large, b)

    def test_no_changed_intro_or_names(self):
        body = "Under the leadership of Director SANTOS, the NBI arrested MARIA DELA CRUZ for alleged fraud. The suspect was brought before the prosecutor for inquest proceedings."
        result = Summarizer().summarize(body)
        self.check_source(body, result)
        self.assertTrue(result.summary.startswith("Under the leadership"))
        self.assertNotIn("convicted", result.summary)

    def test_empty_or_wrong_type(self):
        self.assertEqual(Summarizer().summarize(None).status, "MISSING_BODY")
        self.assertEqual(Summarizer().summarize("  ").summary, "")
        with self.assertRaises(TypeError):
            Summarizer().summarize(17)

    def test_markup_and_footer(self):
        body = '<style>.x{display:none}</style><p>The agency arrested John Smith for alleged fraud.</p><p>The court dismissed the charges after hearing new evidence.</p><p>Media Contact: telephone 123456. Like this: Related.</p>'
        result = Summarizer().summarize(body)
        self.check_source(body, result)
        self.assertNotIn("Media Contact", result.summary)
        self.assertNotIn("<p>", result.summary)

    def test_requested_count_is_supported(self):
        body = "The agency investigated Alpha Company. The company allegedly misrepresented its financial statements. The court convicted the director. Investors received compensation."
        result = Summarizer(sentences=2).summarize(body)
        self.assertLessEqual(len(result.selected_sentences), 2)
        self.check_source(body, result)

    def test_quotes_and_negation_preserved(self):
        body = 'The regulator said, “The company is not authorized to sell investments.” The court has not convicted any director. Investigations are ongoing.'
        result = Summarizer().summarize(body)
        self.check_source(body, result)
        self.assertIn("not authorized", result.summary)

    def test_month_abbreviation_does_not_create_date_fragment(self):
        body = "The survey was conducted Sept. 15 to 23 and published on Oct. 20. The Senate scored 48 points."
        self.assertEqual(len(sentence_split(body)), 2)

    def test_sec_as_agency_can_end_a_sentence(self):
        body = "The firm is not registered with the SEC. In view thereof, investors are advised not to invest."
        self.assertEqual(len(sentence_split(body)), 2)

    def test_bullet_items_preserve_exact_words_and_deadline(self):
        body = "Reporting Format: a. Firms may use Format 1 during the transition; b. Firms may switch to Format X earlier; and, c. From 03 January 2028 Format X is mandatory."
        result = Summarizer().summarize(body)
        self.check_source(body, result)
        self.assertIn("03 January 2028", result.summary)

    def test_website_share_prefix_removed_without_rewriting_lead(self):
        body = "Share on Facebook (Opens in new window) Facebook Share on X (Opens in new window) X The agency arrested John Smith for alleged fraud. The court dismissed the case."
        result = Summarizer().summarize(body)
        self.check_source(body, result)
        self.assertNotIn("Share on", result.summary)

    def test_short_closed_direction_is_not_lost(self):
        body = "This direction has closed. A direction issued on 25 November 2016 replaces it. This direction confirms Andrew Christie as the new commissioner."
        result = Summarizer().summarize(body)
        self.check_source(body, result)
        self.assertIn("has closed", result.summary)


if __name__ == "__main__":
    unittest.main()
