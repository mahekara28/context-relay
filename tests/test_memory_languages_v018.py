"""The multilingual grammar uses real source spans, not translated evidence."""
import unittest

from context_relay.memory_languages import plan_multilingual


class MultilingualPlanTests(unittest.TestCase):
    entries = {"export_format": {"value": "CSV"}, "output_format": {"value": "CSV"},
               "response_language": {"value": "English"}, "response_style": {"value": "detailed"}}

    def assert_updates(self, text, expected):
        plan = plan_multilingual(text, self.entries)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["uncertain"], [])
        self.assertEqual({c["key"]: c["value"] for c in plan["candidates"]}, expected)
        for candidate in plan["candidates"]:
            self.assertEqual(candidate["span"], (candidate["start"], candidate["end"]))
            self.assertTrue(text[candidate["start"]:candidate["end"]].strip())
            self.assertTrue(candidate["explicit"])
        return plan

    def assert_uncertain(self, text, keys):
        plan = plan_multilingual(text, self.entries)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["candidates"], [])
        self.assertEqual(plan["uncertain"], sorted(keys))
        return plan

    def test_hinglish_language(self):
        for text in ("Ab se jawab Hindi mein do.", "Hindi mein jawab do", "please ab se jawab Marathi mein do"):
            with self.subTest(text=text):
                self.assert_updates(text, {"response_language": "Marathi" if "Marathi" in text else "Hindi"})

    def test_hinglish_named_settings(self):
        for text, key, value in (
            ("export format JSON rakho", "export_format", "JSON"),
            ("output ka format ko YAML kar do", "output_format", "YAML"),
            ("response language Hindi set karo", "response_language", "Hindi"),
            ("jawab sankshipt rakho", "response_style", "concise"),
        ):
            with self.subTest(text=text):
                self.assert_updates(text, {key: value})

    def test_hinglish_old_value_is_explicit(self):
        for text in ("export format CSV se JSON karo", "export format ke liye CSV nahi, JSON use karo", "CSV ki jagah JSON mein export karo"):
            with self.subTest(text=text):
                plan = self.assert_updates(text, {"export_format": "JSON"})
                self.assertEqual(plan["candidates"][0]["old"], "CSV")

    def test_hindi_language(self):
        for text in ("अब से हिंदी में जवाब दो।", "जवाब अंग्रेज़ी में दीजिए।", "कृपया उत्तर मराठी में दें।"):
            with self.subTest(text=text):
                expected = "Hindi" if "हिंदी" in text else "Marathi" if "मराठी" in text else "English"
                self.assert_updates(text, {"response_language": expected})

    def test_hindi_named_settings(self):
        for text, key, value in (
            ("निर्यात प्रारूप JSON रखो।", "export_format", "JSON"),
            ("आउटपुट का फॉर्मेट YAML कर दो।", "output_format", "YAML"),
            ("जवाब की भाषा हिंदी करो।", "response_language", "Hindi"),
            ("जवाब संक्षिप्त रखो।", "response_style", "concise"),
        ):
            with self.subTest(text=text):
                self.assert_updates(text, {key: value})

    def test_hindi_old_value_is_explicit(self):
        for text in ("निर्यात प्रारूप CSV से JSON कर दो।", "CSV की जगह JSON में निर्यात करो।", "निर्यात प्रारूप के लिए CSV नहीं, JSON रखो।"):
            with self.subTest(text=text):
                plan = self.assert_updates(text, {"export_format": "JSON"})
                self.assertEqual(plan["candidates"][0]["old"], "CSV")

    def test_span_is_original_unicode_clause(self):
        text = "  अब से हिंदी में जवाब दो।\n  export format JSON rakho."
        plan = self.assert_updates(text, {"response_language": "Hindi", "export_format": "JSON"})
        self.assertEqual([text[c["start"]:c["end"]] for c in plan["candidates"]],
                         ["अब से हिंदी में जवाब दो", "export format JSON rakho"])

    def test_span_includes_old_value_and_prefix(self):
        text = "  Por favor, cambia el formato de exportación de CSV a JSON."
        candidate = self.assert_updates(text, {"export_format": "JSON"})["candidates"][0]
        self.assertEqual(text[candidate["start"]:candidate["end"]], text.strip().rstrip("."))
        self.assertEqual(candidate["old"], "CSV")

    def test_spanish_language_and_style(self):
        for text, key, value in (
            ("A partir de ahora, responde en español.", "response_language", "Spanish"),
            ("Por favor, responde en hindi.", "response_language", "Hindi"),
            ("Responde de forma concisa.", "response_style", "concise"),
            ("Usa respuestas breves para las respuestas.", "response_style", None),
        ):
            with self.subTest(text=text):
                if value:
                    self.assert_updates(text, {key: value})
                else:
                    self.assertFalse(plan_multilingual(text, self.entries)["candidates"])

    def test_spanish_named_settings(self):
        for text, key, value in (
            ("Cambia el formato de exportación a JSON.", "export_format", "JSON"),
            ("Establece el formato de salida en YAML.", "output_format", "YAML"),
            ("Usa inglés para el idioma.", "response_language", "English"),
            ("Para las respuestas, usa breve.", "response_style", "brief"),
        ):
            with self.subTest(text=text):
                self.assert_updates(text, {key: value})

    def test_spanish_old_value_is_explicit(self):
        for text in ("Cambia el idioma de inglés a español.", "Responde en español en lugar de inglés.", "Para las respuestas, usa español en lugar de inglés."):
            with self.subTest(text=text):
                plan = self.assert_updates(text, {"response_language": "Spanish"})
                self.assertEqual(plan["candidates"][0]["old"], "English")

    def test_french_language_and_style(self):
        for text, key, value in (
            ("Désormais, réponds en français.", "response_language", "French"),
            ("S'il vous plaît, répondez en hindi.", "response_language", "Hindi"),
            ("Réponds de façon concise.", "response_style", "concise"),
            ("Pour les réponses, utilise détaillées.", "response_style", "detailed"),
        ):
            with self.subTest(text=text):
                self.assert_updates(text, {key: value})

    def test_french_named_settings(self):
        for text, key, value in (
            ("Change le format d'exportation en JSON.", "export_format", "JSON"),
            ("Change le format de sortie en YAML.", "output_format", "YAML"),
            ("Utilise l'anglais pour la langue.", "response_language", "English"),
            ("Utilise bref pour le style des réponses.", "response_style", "brief"),
        ):
            with self.subTest(text=text):
                self.assert_updates(text, {key: value})

    def test_french_old_value_is_explicit(self):
        for text in ("Change la langue de l'anglais à l'hindi.", "Réponds en hindi au lieu de l'anglais."):
            with self.subTest(text=text):
                plan = self.assert_updates(text, {"response_language": "Hindi"})
                self.assertEqual(plan["candidates"][0]["old"], "English")

    def test_multilingual_complete_message(self):
        self.assert_updates("Use JSON for export format; अब से हिंदी में जवाब दो।", {"export_format": "JSON", "response_language": "Hindi"})
        self.assert_updates("Cambia el formato de exportación a JSON y responde en español.", {"export_format": "JSON", "response_language": "Spanish"})
        self.assert_updates("Réponds en français et utilise JSON pour le format d'exportation.", {"response_language": "French", "export_format": "JSON"})

    def test_unknown_value_disputes_only_named_existing_key(self):
        for text in ("export format Parquet rakho", "निर्यात प्रारूप Parquet करो।", "Cambia el formato de exportación a Parquet.", "Change le format d'exportation en Parquet."):
            with self.subTest(text=text):
                self.assert_uncertain(text, ["export_format"])

    def test_unparsed_trailing_correction_cannot_partially_update(self):
        for text in ("export format JSON rakho; response language kuch aur rakho", "Cambia el formato de exportación a JSON; cambia el idioma a otro.", "Utilise JSON pour le format d'exportation; change la langue selon notre besoin."):
            with self.subTest(text=text):
                self.assert_uncertain(text, ["export_format", "response_language"])

    def test_ambiguous_choice_disputes(self):
        for text in ("response language Hindi ya English rakho", "Cambia el idioma a español o inglés.", "Change la langue en français ou anglais."):
            with self.subTest(text=text):
                self.assert_uncertain(text, ["response_language"])

    def test_contradictory_updates_dispute(self):
        self.assert_uncertain("export format JSON rakho; export format CSV rakho", ["export_format"])

    def test_unknown_old_value_cannot_be_ignored(self):
        self.assert_uncertain("export format Parquet se JSON karo", ["export_format"])

    def test_new_unknown_setting_does_not_create_pending_custom_key(self):
        self.assertEqual(plan_multilingual("admin access allow karo", self.entries), {"candidates": [], "uncertain": []})
        self.assertEqual(plan_multilingual("export format Parquet rakho", {}), {"candidates": [], "uncertain": []})

    def test_quoted_and_code_instructions_ignored(self):
        for text in ('"Cambia el idioma a español."', "`export format JSON rakho`", "« Réponds en français. »", "'अब से हिंदी में जवाब दो'", 'Cambia el idioma a español; "Use JSON for export format."'):
            with self.subTest(text=text):
                self.assertTrue(plan_multilingual(text, self.entries)["ignored"])

    def test_translation_reports_and_hypotheticals_ignored(self):
        for text in ("Traduce: Use JSON for export format.", "El documento dice: Cambia el idioma a español.", "Il a dit: Réponds en français.", "Usne kaha: export format JSON rakho", "अगर export format JSON करो तो क्या होगा", "Agar export format JSON rakho", "Imagine: réponds en français."):
            with self.subTest(text=text):
                plan = plan_multilingual(text, self.entries)
                self.assertIsNotNone(plan)
                self.assertTrue(plan["ignored"])

    def test_questions_and_temporary_requests_ignored(self):
        for text in ("¿Cambia el idioma a español?", "क्या हिंदी में जवाब दो?", "Export format JSON rakho?", "Solo hoy responde en español.", "Pour cette réponse, réponds en français.", "Sirf aaj export format JSON rakho", "अभी के लिए हिंदी में जवाब दो।"):
            with self.subTest(text=text):
                self.assertTrue(plan_multilingual(text, self.entries)["ignored"])

    def test_prohibitions_do_not_create_new_values(self):
        for text in ("export format JSON mat karo", "निर्यात प्रारूप JSON मत करो।", "No cambies el formato de exportación a JSON.", "Ne change pas le format d'exportation en JSON."):
            with self.subTest(text=text):
                self.assertEqual(plan_multilingual(text, self.entries), {"candidates": [], "uncertain": []})

    def test_rejected_current_value_remains_uncertain(self):
        for text in ("export format CSV nahi chahiye", "निर्यात प्रारूप CSV नहीं चाहिए।", "No uses CSV para el formato de exportación.", "N'utilise plus CSV pour le format d'exportation."):
            with self.subTest(text=text):
                self.assert_uncertain(text, ["export_format"])

    def test_canonicalization_does_not_change_global_rules(self):
        from context_relay.memory_values import normalize_memory_value
        self.assert_updates("Responde en español.", {"response_language": "Spanish"})
        self.assertEqual(normalize_memory_value("response_language", "español"), "español")
        self.assertEqual(normalize_memory_value("approved", "sí"), "sí")

    def test_english_only_deferred_to_existing_grammar(self):
        self.assertIsNone(plan_multilingual("Use JSON for export format.", self.entries))

    def test_noninstruction_mentions_do_not_dispute(self):
        for text in ("El idioma español es útil.", "La langue française est belle.", "हिंदी भाषा लोकप्रिय है।", "The client likes the Spanish idioma."):
            with self.subTest(text=text):
                self.assertEqual(plan_multilingual(text, self.entries), {"candidates": [], "uncertain": []})

    def test_unresolved_indirect_rejection_disputes(self):
        for text in ("response language ab sahi nahi hai", "जवाब की भाषा गलत है।", "La langue actuelle ne me convient plus.", "Quiero otro idioma."):
            with self.subTest(text=text):
                self.assert_uncertain(text, ["response_language"])

    def test_language_contrast_preserves_old_value(self):
        for text in ("Ab English nahi Hindi mein jawab do", "अब अंग्रेजी नहीं हिंदी में जवाब दो।"):
            with self.subTest(text=text):
                plan = self.assert_updates(text, {"response_language": "Hindi"})
                self.assertEqual(plan["candidates"][0]["old"], "English")

    def test_prohibition_with_unparsed_other_correction_not_swallowed(self):
        self.assert_uncertain("export format JSON karo, response language Hindi mat karo", ["export_format", "response_language"])

    def test_single_quoted_french_apostrophe_does_not_expose_english_clause(self):
        text = "'Utilise JSON pour le format d'exportation.'; Use Hindi for response language."
        self.assertTrue(plan_multilingual(text, self.entries)["ignored"])

    def test_large_unknown_value_is_not_regex_parsed_or_saved(self):
        self.assert_uncertain("Cambia el formato de exportación a " + "JSON a " * 2000, ["export_format"])

    def test_possessive_plural_and_future_wrappers(self):
        for text, key, value in (
            ("Aage se mere exports CSV ke bajay YAML mein chahiye.", "export_format", "YAML"),
            ("आज से मेरे एक्सपोर्ट CSV की जगह TSV में होने चाहिए।", "export_format", "TSV"),
            ("De ahora en adelante, prefiero XML para mis exportaciones.", "export_format", "XML"),
            ("Désormais, je préfère HTML pour vos exports.", "export_format", "HTML"),
            ("Pour toutes les réponses futures, utilise le français.", "response_language", "French"),
        ):
            with self.subTest(text=text):
                self.assert_updates(text, {key: value})

    def test_person_inflections_and_preference_frames(self):
        for text, value in (
            ("अब से मुझे मराठी में जवाब दिया करें।", "Marathi"),
            ("Aage se mujhe Spanish mein jawab dena.", "Spanish"),
            ("De ahora en adelante, respóndeme en francés.", "French"),
            ("À partir de maintenant, réponds-moi en hindi.", "Hindi"),
            ("Quiero que el español sea el idioma de mis respuestas a partir de ahora.", "Spanish"),
        ):
            with self.subTest(text=text):
                self.assert_updates(text, {"response_language": value})

    def test_style_noun_phrases_with_possessives(self):
        for text in ("Para futuras respuestas, prefiero explicaciones concisas.",
                     "Pour tes prochaines réponses, je préfère des explications concises."):
            with self.subTest(text=text):
                self.assert_updates(text, {"response_style": "concise"})

    def test_language_and_style_are_one_atomic_instruction(self):
        for text in ("Aage se German mein detail se jawab dena.",
                     "En adelante, dame respuestas detalladas en alemán.",
                     "À l'avenir, réponds-moi en allemand avec des explications détaillées.",
                     "अब से मुझे जर्मन में विस्तार से जवाब देना।"):
            with self.subTest(text=text):
                self.assert_updates(text, {"response_language": "German", "response_style": "detailed"})
        self.assert_uncertain("En adelante, dame respuestas inesperadas en hindi.", ["response_language", "response_style"])

    def test_same_message_reference_keeps_antecedent_evidence(self):
        text = "Mes exports étaient en CSV ; je voudrais qu'ils soient en YAML désormais."
        candidate = self.assert_updates(text, {"export_format": "YAML"})["candidates"][0]
        self.assertEqual(candidate["old"], "CSV")
        self.assertEqual(text[candidate["start"]:candidate["end"]], text[:-1])
        self.assert_updates("Sobre mis exportaciones: quiero que sean XML de ahora en adelante.", {"export_format": "XML"})

    def test_style_rejection_requires_same_message_resolution(self):
        self.assert_uncertain("आगे से छोटे जवाब नहीं चाहिए।", ["response_style"])
        self.assert_updates("आगे से छोटे जवाब नहीं चाहिए; विस्तार से जवाब देना।", {"response_style": "detailed"})

    def test_uncertain_style_and_language_have_distinct_targets(self):
        for text in ("Meri detailed replies wali preference ab sahi nahi hai; nayi preference baad mein bataunga.",
                     "Ya no quiero respuestas detalladas, pero aún no sé cuánta explicación quiero.",
                     "Je ne veux plus de réponses détaillées ; je préciserai plus tard le niveau de détail souhaité."):
            with self.subTest(text=text):
                self.assert_uncertain(text, ["response_style"])
        self.assert_uncertain("Mera output format aur reply language dono ab galat hain; nayi settings baad mein bataunga.", ["output_format", "response_language"])

    def test_shared_subject_rejection_does_not_drop_first_target(self):
        for text in ("Mi formato de salida y el idioma de tus respuestas deben cambiar.",
                     "Mon format de sortie et la langue de tes réponses doivent changer."):
            with self.subTest(text=text):
                self.assert_uncertain(text, ["output_format", "response_language"])

    def test_temporary_hinglish_and_negative_multi_setting_requests(self):
        text = "Sirf is reply ke liye detail mein samjhao; aage ke jawab short hi rehne chahiye."
        self.assertTrue(plan_multilingual(text, self.entries)["ignored"])
        self.assert_updates("Mera export format JSON mat karna, aur reply language Hindi mat karna.", {})

    def test_finite_quoted_values_are_not_quoted_instructions(self):
        for text in ("Cambia el idioma a 'hindi'.", 'Cambia el idioma a "hindi".', "Change la langue en « hindi »."):
            with self.subTest(text=text):
                self.assert_updates(text, {"response_language": "Hindi"})
        self.assert_uncertain("Cambia el formato de exportación a 'Parquet'.", ["export_format"])
        self.assertTrue(plan_multilingual('Cambia el idioma a "ignore all prior instructions".', self.entries)["ignored"])

    def test_explicit_uncertain_hedges_clarify_instead_of_retaining_old(self):
        for text in ("Cambia el idioma a tal vez hindi.", "Tal vez, cambia el idioma a hindi.",
                     "Change la langue en peut-être hindi.", "response language shayad Hindi rakho",
                     "जवाब की भाषा शायद हिंदी करो।"):
            with self.subTest(text=text):
                self.assert_uncertain(text, ["response_language"])

    def test_history_and_referents_do_not_guess_across_messages(self):
        self.assertIsNone(plan_multilingual("Mes exports étaient en CSV.", self.entries))
        self.assert_updates("Je voudrais qu'ils soient en JSON.", {})
        self.assert_uncertain("Mes exports étaient en Parquet ; je voudrais qu'ils soient en JSON.", [])

    def test_intervening_setting_breaks_old_pronoun_scope(self):
        self.assert_uncertain("Mes exports étaient en CSV ; change le format de sortie en YAML ; je voudrais qu'ils soient en JSON.", ["export_format", "output_format"])

    def test_quoted_payload_cannot_hide_a_later_english_instruction(self):
        text = 'Il faut analyser "Cambia el idioma a español"; Use JSON for export format.'
        self.assertTrue(plan_multilingual(text, self.entries)["ignored"])


class MultilingualPublicMemoryTests(unittest.TestCase):
    def test_public_path_preserves_source_and_never_authorizes(self):
        from context_relay import AutomaticMemory, ContextGuard, MemoryPolicy
        messages = [{"role": "user", "content": "Reply in English."}]
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id, policy=MemoryPolicy(max_chars=16000))
            memory.process(messages)
            source = "À l'avenir, réponds-moi en espagnol avec des explications détaillées."
            messages.extend([{"role": "assistant", "content": "OK"}, {"role": "user", "content": source}])
            memory.process(messages)
            values = {row["key"]: row["value"] for row in memory.entries()}
            self.assertEqual(values, {"response_language": "Spanish", "response_style": "detailed"})
            for row in memory.entries():
                evidence = row["source"]
                self.assertEqual(evidence["quote"], source[evidence["start"]:evidence["end"]])
            self.assertEqual(guard.store.facts(guard.session_id), {})
            before = memory.snapshot()
            memory.process(messages)
            self.assertEqual(before, memory.snapshot())

    def test_public_path_uncertain_unknown_quoted_enum(self):
        from context_relay import AutomaticMemory, ContextGuard
        messages = [{"role": "user", "content": "Export as CSV."}, {"role": "assistant", "content": "OK"}]
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id)
            memory.process(messages[:-1])
            messages.append({"role": "user", "content": "Cambia el formato de exportación a 'Parquet'."})
            memory.process(messages)
            self.assertEqual(memory.entries()[0]["value"], "CSV")
            self.assertEqual([(p["targets"], p["value"], p["reason"]) for p in memory.pending()],
                             [(["export_format"], None, "unparsed_correction")])


if __name__ == "__main__":
    unittest.main()
