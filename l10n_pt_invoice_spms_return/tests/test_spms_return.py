# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io
from datetime import date

import openpyxl

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import SavepointCase, new_test_user

EXCEL_HEADERS = (
    "NUMFACTURA",
    "NUMEROPRESCRICAO",
    "VALORTOTAL",
    "VALORTOTALAPURADO",
    "VALORTOTALAPURADOIVA",
    "QUANTIDADETOTAL",
    "Numero de dias pagos",
    "COD_ERRO",
    "DESC_ERRO",
    "SISTEMAPRESTADOCRD",
)


class TestSpmsReturn(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.env.user.groups_id |= cls.env.ref(
            "l10n_pt_invoice_spms_return.spms_return_group_responsible"
        )
        cls.tax6 = cls.env["account.tax"].create(
            {
                "name": "IVA 6% (OBS) test",
                "amount_type": "percent",
                "amount": 6.0,
                "type_tax_use": "sale",
                "company_id": cls.company.id,
            }
        )
        cls.income_account = cls.env["account.account"].search(
            [
                ("company_id", "=", cls.company.id),
                (
                    "user_type_id",
                    "=",
                    cls.env.ref("account.data_account_type_revenue").id,
                ),
            ],
            limit=1,
        )
        journal_vals = {
            "name": "SPMS Test Sales",
            "code": "TSPMS",
            "type": "sale",
            "company_id": cls.company.id,
            "default_account_id": cls.income_account.id,
        }
        if "edi_format_ids" in cls.env["account.journal"]._fields:
            journal_vals["edi_format_ids"] = [(5, 0, 0)]
        cls.journal = cls.env["account.journal"].create(journal_vals)
        cls.partner = cls.env["res.partner"].create({"name": "SPMS Test Partner"})
        cls.product = cls.env["product.product"].create(
            {
                "name": "Oxygen therapy test",
                "type": "service",
                "taxes_id": [(6, 0, cls.tax6.ids)],
            }
        )
        # the SPMS flow relies on global tax rounding (production setting)
        cls.company.tax_calculation_rounding_method = "round_globally"
        # created but NOT configured on the company: each adjustment test
        # assigns it explicitly, the rest exercise the unconfigured path
        cls.adjustment_product = cls.env["product.product"].create(
            {
                "name": "SPMS adjustment test",
                "type": "service",
                "taxes_id": [(6, 0, cls.tax6.ids)],
            }
        )

    @classmethod
    def _create_invoice(cls, name, lines):
        """Post an SPMS-like customer invoice.

        lines: list of (prescription, days, price_unit[, tax]) tuples; every
        line carries the SPMS period dates so the total-rejection case can
        assert they are preserved on the refund.
        """
        move = cls.env["account.move"].create(
            {
                "name": name,
                "move_type": "out_invoice",
                "partner_id": cls.partner.id,
                "journal_id": cls.journal.id,
                "invoice_date": date(2026, 5, 31),
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product.id,
                            "quantity": days,
                            "price_unit": price_unit,
                            "tax_ids": [(6, 0, (extra[0] if extra else cls.tax6).ids)],
                            "spms_prescription": prescription,
                            "spms_start_date": date(2026, 5, 1),
                            "spms_end_date": date(2026, 5, 31),
                        },
                    )
                    for prescription, days, price_unit, *extra in lines
                ],
            }
        )
        move.action_post()
        return move

    @classmethod
    def _make_excel(cls, rows):
        """Encode rows as the SNS/CCMSNS error report (base64 xlsx).

        rows: list of dicts keyed like the parser output; only NUMFACTURA is
        mandatory, the rest defaults to empty/zero as in the real report.
        """
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(EXCEL_HEADERS)
        for row in rows:
            sheet.append(
                (
                    row.get("invoice_number"),
                    row.get("prescription"),
                    row.get("billed", 0.0),
                    row.get("allowed", 0.0),
                    row.get("allowed_taxed", 0.0),
                    row.get("days_billed", 0.0),
                    row.get("days_paid", ""),
                    row.get("code", "C010"),
                    row.get("description", "Test error"),
                    row.get("provider_ref", ""),
                )
            )
        buffer = io.BytesIO()
        workbook.save(buffer)
        return base64.b64encode(buffer.getvalue())

    @classmethod
    def _create_return(cls, rows, period="202605", process=True):
        rec = cls.env["spms.return"].create(
            {
                "period": period,
                "file": cls._make_excel(rows),
                "file_name": "OXIGEN %s.xlsx" % period,
                "company_id": cls.company.id,
            }
        )
        if process:
            rec.action_process()
        return rec

    @classmethod
    def _standard_rows(cls):
        """Three prescriptions of FT2026-123 covering the §5 line cases.

        P1: total rejection (V=0)          -> diff 31,00 (case 1)
        P2: partial with reliable days     -> diff 4,00 (case 2, 2 days)
        P3: partial without paid days      -> diff 1,00 (case 3 fallback)
        Estimate: 32,86 + 4,24 + 1,06 = 38,16 = the itemised draft total.
        """
        return [
            {
                "invoice_number": "FT2026-123",
                "prescription": "TESTP001",
                "billed": 31.0,
                "allowed": 0.0,
                "allowed_taxed": 0.0,
                "days_billed": 31.0,
            },
            {
                "invoice_number": "FT2026-123",
                "prescription": "TESTP002",
                "billed": 62.0,
                "allowed": 58.0,
                "allowed_taxed": 61.48,
                "days_billed": 31.0,
                "days_paid": 29.0,
            },
            {
                "invoice_number": "FT2026-123",
                "prescription": "TESTP003",
                "billed": 30.0,
                "allowed": 29.0,
                "allowed_taxed": 30.74,
                "days_billed": 10.0,
            },
        ]

    @classmethod
    def _standard_invoice(cls):
        return cls._create_invoice(
            "FT 2026/00123",
            [
                ("TESTP001", 31, 1.0),
                ("TESTP002", 31, 2.0),
                ("TESTP003", 10, 3.0),
            ],
        )

    def test_period_format_constraint(self):
        with self.assertRaises(ValidationError):
            self.env["spms.return"].create({"period": "2026-5"})

    def test_process_requires_file(self):
        rec = self.env["spms.return"].create({"period": "202605"})
        with self.assertRaisesRegex(UserError, "Upload the error file"):
            rec.action_process()

    def test_process_requires_responsible(self):
        plain_user = new_test_user(self.env, login="spms_plain_user")
        rec = self._create_return(self._standard_rows(), process=False)
        with self.assertRaises(AccessError):
            rec.with_user(plain_user).action_process()

    def test_process_missing_columns(self):
        workbook = openpyxl.Workbook()
        workbook.active.append(("NUMFACTURA", "NUMEROPRESCRICAO"))
        workbook.active.append(("FT2026-123", "TESTP001"))
        buffer = io.BytesIO()
        workbook.save(buffer)
        rec = self.env["spms.return"].create(
            {
                "period": "202605",
                "file": base64.b64encode(buffer.getvalue()),
            }
        )
        with self.assertRaisesRegex(UserError, "VALORTOTAL"):
            rec.action_process()

    def test_process_matches_and_estimates(self):
        move = self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        self.assertEqual(rec.state, "processed")
        self.assertEqual(len(rec.invoice_ids), 1)
        invoice = rec.invoice_ids
        self.assertEqual(invoice.name, "FT2026-123")
        self.assertEqual(invoice.move_id, move)
        self.assertEqual(invoice.state, "awaiting_official")
        self.assertEqual(len(invoice.line_ids), 3)
        self.assertEqual(set(invoice.line_ids.mapped("state")), {"matched"})
        for line in invoice.line_ids:
            self.assertEqual(line.move_line_id.spms_prescription, line.prescription)
        self.assertAlmostEqual(invoice.credit_estimated, 38.16)
        self.assertAlmostEqual(invoice.amount_lines_untaxed, 36.0)
        self.assertEqual(invoice.error_codes, "C010")

    def test_estimate_uses_line_tax(self):
        tax23 = self.env["account.tax"].create(
            {
                "name": "IVA 23% test",
                "amount_type": "percent",
                "amount": 23.0,
                "type_tax_use": "sale",
                "company_id": self.company.id,
            }
        )
        self._create_invoice(
            "FT 2026/00124",
            [("TESTP004", 31, 1.0), ("TESTP005", 10, 1.0, tax23)],
        )
        rec = self._create_return(
            [
                {
                    "invoice_number": "FT2026-124",
                    "prescription": "TESTP004",
                    "billed": 31.0,
                    "allowed": 0.0,
                    "allowed_taxed": 0.0,
                },
                {
                    "invoice_number": "FT2026-124",
                    "prescription": "TESTP005",
                    "billed": 10.0,
                    "allowed": 0.0,
                    "allowed_taxed": 0.0,
                },
            ]
        )
        lines = rec.invoice_ids.line_ids
        self.assertEqual(set(lines.mapped("state")), {"matched"})
        line6 = lines.filtered(lambda line: line.prescription == "TESTP004")
        line23 = lines.filtered(lambda line: line.prescription == "TESTP005")
        self.assertAlmostEqual(line6.amount_credit_taxed, 32.86)
        self.assertAlmostEqual(line23.amount_credit_taxed, 12.30)
        self.assertAlmostEqual(rec.invoice_ids.credit_estimated, 45.16)

    def test_estimate_unmatched_line_shows_no_taxed_amount(self):
        # no tax rate is ever assumed: an unmatched line shows no with-VAT
        # value (even with the adjustment product configured) and the header
        # estimate covers matched lines only
        self.company.spms_adjustment_product_id = self.adjustment_product
        self._create_invoice("FT 2026/00124", [("TESTP004", 31, 1.0)])
        rec = self._create_return(
            [
                {
                    "invoice_number": "FT2026-124",
                    "prescription": "TESTP004",
                    "billed": 31.0,
                    "allowed": 0.0,
                    "allowed_taxed": 0.0,
                },
                {
                    "invoice_number": "FT2026-124",
                    "prescription": "TESTP010",
                    "billed": 10.0,
                    "allowed": 0.0,
                    "allowed_taxed": 0.0,
                },
            ]
        )
        invoice = rec.invoice_ids
        matched = invoice.line_ids.filtered(
            lambda line: line.prescription == "TESTP004"
        )
        unmatched = invoice.line_ids.filtered(
            lambda line: line.prescription == "TESTP010"
        )
        self.assertEqual(unmatched.state, "not_found")
        self.assertFalse(unmatched.amount_credit_taxed)
        self.assertAlmostEqual(matched.amount_credit_taxed, 32.86)
        self.assertAlmostEqual(invoice.credit_estimated, 32.86)

    def test_estimate_rounds_per_tax_group(self):
        # the header estimate rounds once per tax group, exactly like the
        # draft Odoo will compute: 6% on 9,10 -> 0,55 and 23% on 6,33 ->
        # 1,46 (a single global rounding would yield 17,43 instead of 17,44)
        tax23 = self.env["account.tax"].create(
            {
                "name": "IVA 23% test",
                "amount_type": "percent",
                "amount": 23.0,
                "type_tax_use": "sale",
                "company_id": self.company.id,
            }
        )
        self._create_invoice(
            "FT 2026/00125",
            [("TESTP006", 1, 9.10), ("TESTP007", 1, 6.33, tax23)],
        )
        rec = self._create_return(
            [
                {
                    "invoice_number": "FT2026-125",
                    "prescription": "TESTP006",
                    "billed": 9.10,
                    "allowed": 0.0,
                    "allowed_taxed": 0.0,
                },
                {
                    "invoice_number": "FT2026-125",
                    "prescription": "TESTP007",
                    "billed": 6.33,
                    "allowed": 0.0,
                    "allowed_taxed": 0.0,
                },
            ]
        )
        self.assertAlmostEqual(rec.invoice_ids.credit_estimated, 17.44)

    def test_process_not_found_and_zero_diff(self):
        rec = self._create_return(
            [
                {
                    "invoice_number": "FT2026-999",
                    "prescription": "TESTP009",
                    "billed": 10.0,
                    "allowed": 10.0,
                    "allowed_taxed": 10.6,
                },
            ]
        )
        invoice = rec.invoice_ids
        self.assertFalse(invoice.move_id)
        self.assertEqual(invoice.state, "not_found")
        self.assertEqual(invoice.line_ids.state, "not_found")

    def test_process_incoherent_duplicate_rows(self):
        self._standard_invoice()
        rows = self._standard_rows()[:1]
        duplicate = dict(rows[0], billed=99.0, code="C313")
        rec = self._create_return(rows + [duplicate])
        line = rec.invoice_ids.line_ids
        self.assertEqual(len(line), 1)
        self.assertEqual(line.state, "data_error")
        self.assertEqual(line.data_error_reason, "incoherent")
        self.assertEqual(len(line.error_ids), 2)
        self.assertEqual(line.error_codes, "C010 / C313")

    def test_process_empty_amount_marks_data_error(self):
        self._standard_invoice()
        rows = self._standard_rows()[:2]
        rows[1]["allowed_taxed"] = None
        rec = self._create_return(rows)
        lines = rec.invoice_ids.line_ids
        intact = lines.filtered(lambda line: line.prescription == "TESTP001")
        broken = lines.filtered(lambda line: line.prescription == "TESTP002")
        self.assertEqual(intact.state, "matched")
        self.assertEqual(broken.state, "data_error")
        self.assertEqual(broken.data_error_reason, "missing")

    def test_process_diverged_amounts_marks_data_error(self):
        self._standard_invoice()
        rows = self._standard_rows()[:2]
        rows[1]["allowed_taxed"] = 60.0  # 58.00 x 1.06 = 61.48, not 60.00
        rec = self._create_return(rows)
        lines = rec.invoice_ids.line_ids
        intact = lines.filtered(lambda line: line.prescription == "TESTP001")
        broken = lines.filtered(lambda line: line.prescription == "TESTP002")
        self.assertEqual(intact.state, "matched")
        self.assertEqual(broken.state, "data_error")
        self.assertEqual(broken.data_error_reason, "diverged")

    def test_process_unmatched_diverged_left_not_found(self):
        # without a matched line there is no tax to judge W against: the
        # coherence check is deferred until the invoice exists
        rec = self._create_return(
            [
                {
                    "invoice_number": "FT2026-999",
                    "prescription": "TESTP009",
                    "billed": 62.0,
                    "allowed": 58.0,
                    "allowed_taxed": 60.0,
                },
            ]
        )
        line = rec.invoice_ids.line_ids
        self.assertEqual(line.state, "not_found")
        self.assertFalse(line.data_error_reason)

    def test_process_duplicate_rows_taxed_contradiction_marks_data_error(self):
        self._standard_invoice()
        rows = self._standard_rows()[:1]
        duplicate = dict(rows[0], allowed_taxed=33.0, code="C313")
        rec = self._create_return(rows + [duplicate])
        line = rec.invoice_ids.line_ids
        self.assertEqual(len(line), 1)
        self.assertEqual(line.state, "data_error")
        self.assertEqual(line.data_error_reason, "diverged")

    def test_process_text_amount_marks_data_error(self):
        self._standard_invoice()
        rows = self._standard_rows()[:2]
        rows[1]["allowed_taxed"] = "1,50"
        rec = self._create_return(rows)
        lines = rec.invoice_ids.line_ids
        intact = lines.filtered(lambda line: line.prescription == "TESTP001")
        broken = lines.filtered(lambda line: line.prescription == "TESTP002")
        self.assertEqual(intact.state, "matched")
        self.assertEqual(broken.state, "data_error")
        self.assertEqual(broken.data_error_reason, "unconvertible")

    def test_process_date_amount_marks_data_error(self):
        self._standard_invoice()
        rows = self._standard_rows()[:2]
        rows[1]["billed"] = date(2026, 5, 31)
        rec = self._create_return(rows)
        lines = rec.invoice_ids.line_ids
        intact = lines.filtered(lambda line: line.prescription == "TESTP001")
        broken = lines.filtered(lambda line: line.prescription == "TESTP002")
        self.assertEqual(intact.state, "matched")
        self.assertEqual(broken.state, "data_error")
        self.assertEqual(broken.data_error_reason, "unconvertible")

    def test_data_error_holds_invoice_back_from_ready(self):
        self._standard_invoice()
        rows = self._standard_rows()
        rows[1]["allowed_taxed"] = "1,50"
        rec = self._create_return(rows)
        invoice = rec.invoice_ids
        self.assertEqual(invoice.state, "mismatch")
        invoice.credit_official = 38.16
        self.assertTrue(invoice.official_confirmed)
        self.assertEqual(invoice.state, "mismatch")
        rec.action_back_to_draft()
        rec.file = self._make_excel(self._standard_rows())
        rec.action_process()
        invoice = rec.invoice_ids
        self.assertEqual(invoice.state, "ready")
        self.assertAlmostEqual(invoice.credit_official, 38.16)

    def test_broken_lines_hold_invoice_back_from_ready(self):
        self._standard_invoice()
        self._create_invoice(
            "FT 2026/00124",
            [("TESTP009", 5, 1.0), ("TESTP009", 5, 1.0)],
        )
        rows = self._standard_rows()[:1]
        rows[0]["prescription"] = "TESTMISSING"
        rows.append(
            {
                "invoice_number": "FT2026-124",
                "prescription": "TESTP009",
                "billed": 10.0,
                "allowed": 5.0,
                "allowed_taxed": 5.3,
                "days_billed": 10.0,
            }
        )
        rec = self._create_return(rows)
        not_found = rec.invoice_ids.filtered(lambda inv: inv.name == "FT2026-123")
        ambiguous = rec.invoice_ids.filtered(lambda inv: inv.name == "FT2026-124")
        self.assertEqual(not_found.line_ids.state, "not_found")
        self.assertEqual(ambiguous.line_ids.state, "ambiguous")
        (not_found + ambiguous).write({"credit_official": 10.6})
        self.assertTrue(not_found.official_confirmed)
        self.assertEqual(not_found.state, "mismatch")
        self.assertEqual(ambiguous.state, "mismatch")

    def test_resolution_locked_while_credit_note_alive(self):
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.credit_official = 38.16
        rec.action_create_credit_notes()
        self.assertEqual(invoice.state, "done")
        line = invoice.line_ids.filtered(lambda line: line.prescription == "TESTP001")
        with self.assertRaisesRegex(UserError, "cancel that credit note"):
            line.resolution = "duplicate"
        invoice.credit_note_move_id.button_cancel()
        line.resolution = "duplicate"
        self.assertEqual(invoice.state, "ready")

    def test_spms_number_collision_blocks_processing(self):
        self._standard_invoice()
        self._create_invoice("FT 2026/0123", [("TESTP009", 5, 1.0)])
        with self.assertRaisesRegex(UserError, "more than one posted invoice"):
            self._create_return(self._standard_rows())

    def test_responsible_user_natural_flow(self):
        self._standard_invoice()
        user = new_test_user(
            self.env,
            login="spms_responsible_user",
            groups="l10n_pt_invoice_spms_return.spms_return_group_responsible",
        )
        rec = (
            self.env["spms.return"]
            .with_user(user)
            .create(
                {
                    "period": "202605",
                    "file": self._make_excel(self._standard_rows()),
                    "file_name": "OXIGEN 202605.xlsx",
                    "company_id": self.company.id,
                }
            )
        )
        rec.action_process()
        invoice = rec.invoice_ids
        invoice.credit_official = 38.16
        rec.action_create_credit_notes()
        self.assertEqual(invoice.state, "done")
        self.assertEqual(invoice.credit_note_move_id.state, "draft")

    def test_processed_return_file_and_period_locked(self):
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        with self.assertRaisesRegex(UserError, "draft SPMS return"):
            rec.period = "202601"
        with self.assertRaisesRegex(UserError, "draft SPMS return"):
            rec.file = self._make_excel(self._standard_rows())

    def test_processed_return_cannot_be_deleted(self):
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        with self.assertRaisesRegex(UserError, "can be deleted"):
            rec.unlink()

    def test_draft_return_edit_and_delete_allowed(self):
        rec = self._create_return(self._standard_rows(), process=False)
        rec.period = "202601"
        rec.file = self._make_excel(self._standard_rows())
        rec.unlink()
        self.assertFalse(rec.exists())

    def test_processed_return_invoice_cannot_be_deleted(self):
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        with self.assertRaisesRegex(UserError, "draft or cancelled"):
            rec.invoice_ids.unlink()
        rec.action_process()
        self.assertTrue(rec.invoice_ids)

    def test_done_invoice_official_locked_while_credit_note_alive(self):
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 38.16})
        rec.action_create_credit_notes()
        with self.assertRaisesRegex(UserError, "cancel that credit note"):
            invoice.credit_official = 40.0
        invoice.credit_note_move_id.button_cancel()
        invoice.credit_official = 40.0
        self.assertEqual(invoice.state, "ready")

    def test_official_manual_write_autostamps(self):
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 38.16})
        self.assertTrue(invoice.official_confirmed)
        self.assertEqual(invoice.official_source, "manual")
        self.assertEqual(invoice.official_date, fields.Date.context_today(invoice))
        self.assertEqual(invoice.state, "ready")

    def test_generate_requires_ready_invoice(self):
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        with self.assertRaisesRegex(UserError, "no ready"):
            rec.action_create_credit_notes()

    def test_generate_creates_credit_note(self):
        move = self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 38.16})
        result = rec.action_create_credit_notes()
        self.assertEqual(result["params"]["type"], "success")
        draft = invoice.credit_note_move_id
        self.assertTrue(draft)
        self.assertEqual(draft.state, "draft")
        self.assertEqual(draft.move_type, "out_refund")
        self.assertEqual(draft.reversed_entry_id, move)
        self.assertEqual(draft.invoice_origin, move.name)
        self.assertAlmostEqual(draft.amount_total, 38.16)
        self.assertEqual(invoice.state, "done")
        self.assertEqual(rec.state, "done")
        by_prescription = {
            line.spms_prescription: line for line in draft.invoice_line_ids
        }
        self.assertEqual(len(by_prescription), 3)
        total_rejection = by_prescription["TESTP001"]
        self.assertEqual(total_rejection.quantity, 31)
        self.assertAlmostEqual(total_rejection.price_unit, 1.0)
        self.assertEqual(total_rejection.spms_start_date, date(2026, 5, 1))
        self.assertEqual(total_rejection.spms_end_date, date(2026, 5, 31))
        partial_days = by_prescription["TESTP002"]
        self.assertEqual(partial_days.quantity, 2)
        self.assertAlmostEqual(partial_days.price_unit, 2.0)
        self.assertFalse(partial_days.spms_start_date)
        fallback = by_prescription["TESTP003"]
        self.assertEqual(fallback.quantity, 1)
        self.assertAlmostEqual(fallback.price_unit, 1.0)
        self.assertFalse(fallback.spms_start_date)
        for line in invoice.line_ids:
            self.assertEqual(
                line.refund_move_line_id,
                by_prescription[line.prescription],
            )

    def test_generate_adjustment_requires_product(self):
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 38.15})
        with self.assertRaisesRegex(
            UserError, "Configure the SPMS adjustment line product"
        ):
            rec.action_create_credit_notes()
        self.assertFalse(invoice.credit_note_move_id)
        self.assertEqual(invoice.state, "ready")
        self.assertEqual(rec.state, "processed")
        self.assertFalse(
            self.env["account.move"].search(
                [
                    ("move_type", "=", "out_refund"),
                    ("company_id", "=", self.company.id),
                ]
            )
        )

    def test_generate_confirmed_without_value_blocks(self):
        # confirming with no value means there is nothing to credit; the
        # guard must block before any draft is created
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"official_confirmed": True})
        self.assertEqual(invoice.state, "ready")
        with self.assertRaisesRegex(UserError, "Nothing to credit"):
            rec.action_create_credit_notes()

    def test_generate_official_zero_blocks(self):
        # a zero official value must not create a credit note even with the
        # adjustment product configured: the guard fires before the draft
        self.company.spms_adjustment_product_id = self.adjustment_product
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 0.0})
        self.assertTrue(invoice.official_confirmed)
        self.assertEqual(invoice.state, "ready")
        with self.assertRaisesRegex(UserError, "Nothing to credit"):
            rec.action_create_credit_notes()
        self.assertFalse(invoice.credit_note_move_id)

    def test_generate_official_exceeds_total_blocks(self):
        # an official value above the original invoice total can only be a
        # data-entry error: the guard fires before the draft, even with the
        # adjustment product configured
        self.company.spms_adjustment_product_id = self.adjustment_product
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 200.0})
        self.assertEqual(invoice.state, "ready")
        with self.assertRaisesRegex(
            UserError, "exceeds the total of the original invoice"
        ):
            rec.action_create_credit_notes()
        self.assertFalse(invoice.credit_note_move_id)

    def test_generate_official_equals_total_allowed(self):
        # a full rejection is legitimate: official == invoice total must
        # generate (the guard is strictly greater-than)
        self.company.spms_adjustment_product_id = self.adjustment_product
        move = self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": move.amount_total})
        rec.action_create_credit_notes()
        self.assertEqual(invoice.state, "done")
        self.assertAlmostEqual(
            invoice.credit_note_move_id.amount_total, move.amount_total
        )

    def test_generate_adjustment_line_direct_base(self):
        self.company.spms_adjustment_product_id = self.adjustment_product
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 38.15})
        rec.action_create_credit_notes()
        draft = invoice.credit_note_move_id
        self.assertAlmostEqual(draft.amount_total, 38.15)
        adjustment = draft.invoice_line_ids.filtered(
            lambda line: line.product_id == self.adjustment_product
        )
        self.assertEqual(len(adjustment), 1)
        self.assertAlmostEqual(adjustment.price_unit, -0.01)
        self.assertEqual(adjustment.tax_ids, self.tax6)
        self.assertEqual(len(draft.invoice_line_ids), 4)
        self.assertEqual(invoice.state, "done")

    def _adjustment_single_line_return(self, name, number, prescription):
        """One-line invoice tuned for cent-edge cases: base 10.75 gives an
        exact tax of 0.645, so the natural credit note totals 11.40 and the
        totals reachable by moving the base jump from 11.38 to 11.40."""
        self._create_invoice(name, [(prescription, 1, 10.75)])
        return self._create_return(
            [
                {
                    "invoice_number": number,
                    "prescription": prescription,
                    "billed": 10.75,
                    "allowed": 0.0,
                    "allowed_taxed": 0.0,
                    "days_billed": 1.0,
                }
            ]
        )

    def test_generate_adjustment_candidate_loop(self):
        self.company.spms_adjustment_product_id = self.adjustment_product
        rec = self._adjustment_single_line_return(
            "FT 2026/00126", "FT2026-126", "TESTP006"
        )
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 11.38})
        rec.action_create_credit_notes()
        draft = invoice.credit_note_move_id
        self.assertAlmostEqual(draft.amount_total, 11.38)
        adjustment = draft.invoice_line_ids.filtered(
            lambda line: line.product_id == self.adjustment_product
        )
        # the theoretical base -0.02 yields 11.37: the candidate loop must
        # land on -0.01
        self.assertAlmostEqual(adjustment.price_unit, -0.01)
        self.assertEqual(invoice.state, "done")

    def test_generate_forced_tax_amount(self):
        self.company.spms_adjustment_product_id = self.adjustment_product
        rec = self._adjustment_single_line_return(
            "FT 2026/00127", "FT2026-127", "TESTP007"
        )
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 11.39})
        rec.action_create_credit_notes()
        draft = invoice.credit_note_move_id
        self.assertAlmostEqual(draft.amount_total, 11.39)
        adjustment = draft.invoice_line_ids.filtered(
            lambda line: line.product_id == self.adjustment_product
        )
        # no base reaches 11.39: the theoretical base is restored and the
        # remaining cent is imposed on the group tax line
        self.assertAlmostEqual(adjustment.price_unit, -0.01)
        tax_line = draft.line_ids.filtered(lambda line: line.tax_line_id == self.tax6)
        self.assertAlmostEqual(tax_line.debit, 0.65)
        self.assertAlmostEqual(
            sum(draft.line_ids.mapped("debit")),
            sum(draft.line_ids.mapped("credit")),
        )
        self.assertEqual(invoice.state, "done")

    def test_generate_adjustment_product_needs_single_tax(self):
        tax23 = self.env["account.tax"].create(
            {
                "name": "IVA 23% test",
                "amount_type": "percent",
                "amount": 23.0,
                "type_tax_use": "sale",
                "company_id": self.company.id,
            }
        )
        self.adjustment_product.taxes_id = [(6, 0, (self.tax6 | tax23).ids)]
        self.company.spms_adjustment_product_id = self.adjustment_product
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 38.15})
        with self.assertRaisesRegex(UserError, "exactly one percentage customer tax"):
            rec.action_create_credit_notes()

    def test_generate_partial_batch(self):
        self._standard_invoice()
        self._create_invoice("FT 2026/00124", [("TESTP004", 10, 1.0)])
        rows = self._standard_rows() + [
            {
                "invoice_number": "FT2026-124",
                "prescription": "TESTP004",
                "billed": 10.0,
                "allowed": 5.0,
                "allowed_taxed": 5.3,
                "days_billed": 10.0,
            }
        ]
        rec = self._create_return(rows)
        good = rec.invoice_ids.filtered(lambda r: r.name == "FT2026-123")
        bad = rec.invoice_ids.filtered(lambda r: r.name == "FT2026-124")
        good.write({"credit_official": 38.16})
        bad.write({"credit_official": 1.0})
        result = rec.action_create_credit_notes()
        self.assertEqual(result["params"]["type"], "warning")
        self.assertTrue(result["params"]["sticky"])
        self.assertIn("FT2026-124", result["params"]["message"])
        self.assertTrue(good.credit_note_move_id)
        self.assertEqual(good.state, "done")
        self.assertFalse(bad.credit_note_move_id)
        self.assertEqual(bad.state, "ready")
        self.assertEqual(rec.state, "processed")

    def test_reprocess_preserves_official_and_done(self):
        self._standard_invoice()
        self._create_invoice("FT 2026/00124", [("TESTP004", 10, 1.0)])
        rows = self._standard_rows() + [
            {
                "invoice_number": "FT2026-124",
                "prescription": "TESTP004",
                "billed": 10.0,
                "allowed": 5.0,
                "allowed_taxed": 5.3,
                "days_billed": 10.0,
            }
        ]
        rec = self._create_return(rows)
        generated = rec.invoice_ids.filtered(lambda r: r.name == "FT2026-123")
        generated.write({"credit_official": 38.16})
        rec.action_create_credit_notes()
        draft = generated.credit_note_move_id
        self.assertEqual(rec.state, "processed")
        rec.action_process()
        generated = rec.invoice_ids.filtered(lambda r: r.name == "FT2026-123")
        pending = rec.invoice_ids.filtered(lambda r: r.name == "FT2026-124")
        self.assertEqual(generated.state, "done")
        self.assertEqual(generated.credit_note_move_id, draft)
        self.assertAlmostEqual(generated.credit_official, 38.16)
        self.assertTrue(generated.official_confirmed)
        by_prescription = {
            line.spms_prescription: line for line in draft.invoice_line_ids
        }
        for line in generated.line_ids:
            self.assertEqual(
                line.refund_move_line_id,
                by_prescription[line.prescription],
            )
        self.assertEqual(pending.state, "awaiting_official")
        self.assertFalse(pending.official_confirmed)

    def _reopen_setup(self):
        """Return with a generated invoice plus a pending one.

        The pending invoice keeps the return in 'processed', mirroring the
        real files where not_found invoices never let it close.
        """
        self._standard_invoice()
        self._create_invoice("FT 2026/00124", [("TESTP004", 10, 1.0)])
        rows = self._standard_rows() + [
            {
                "invoice_number": "FT2026-124",
                "prescription": "TESTP004",
                "billed": 10.0,
                "allowed": 5.0,
                "allowed_taxed": 5.3,
                "days_billed": 10.0,
            }
        ]
        rec = self._create_return(rows)
        generated = rec.invoice_ids.filtered(lambda r: r.name == "FT2026-123")
        generated.write({"credit_official": 38.16})
        rec.action_create_credit_notes()
        return rec, generated

    def test_credit_note_cancelled_reopens_invoice(self):
        # a cancelled credit note must not leave a stale 'done': the next
        # evaluation reopens the invoice keeping the official value
        rec, generated = self._reopen_setup()
        generated.credit_note_move_id.button_cancel()
        rec.action_process()
        generated = rec.invoice_ids.filtered(lambda r: r.name == "FT2026-123")
        self.assertEqual(generated.state, "ready")
        self.assertFalse(generated.credit_note_move_id)
        self.assertAlmostEqual(generated.credit_official, 38.16)
        self.assertTrue(generated.official_confirmed)

    def test_credit_note_deleted_reopens_invoice(self):
        rec, generated = self._reopen_setup()
        generated.credit_note_move_id.unlink()
        rec.action_process()
        generated = rec.invoice_ids.filtered(lambda r: r.name == "FT2026-123")
        self.assertEqual(generated.state, "ready")
        self.assertFalse(generated.credit_note_move_id)
        self.assertAlmostEqual(generated.credit_official, 38.16)

    def test_done_return_with_live_notes_stays_closed(self):
        # the button is also visible on done returns (to regenerate after a
        # cancelled/deleted credit note); with every credit note alive the
        # batch must keep the return closed and block
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 38.16})
        rec.action_create_credit_notes()
        self.assertEqual(rec.state, "done")
        with self.assertRaisesRegex(UserError, "processed SPMS return"):
            rec.action_create_credit_notes()
        self.assertEqual(rec.state, "done")
        self.assertEqual(invoice.state, "done")

    def test_credit_note_cancelled_regenerates_in_batch(self):
        # single-invoice return: generation closes it to 'done'; cancelling
        # the credit note and running the batch again must reopen the
        # return, regenerate and close it back
        self._standard_invoice()
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        invoice.write({"credit_official": 38.16})
        rec.action_create_credit_notes()
        self.assertEqual(rec.state, "done")
        first_draft = invoice.credit_note_move_id
        first_draft.button_cancel()
        rec.action_create_credit_notes()
        self.assertEqual(invoice.state, "done")
        self.assertNotEqual(invoice.credit_note_move_id, first_draft)
        self.assertEqual(invoice.credit_note_move_id.state, "draft")
        self.assertEqual(first_draft.state, "cancel")
        self.assertEqual(rec.state, "done")

    def test_preexisting_credit_note_state(self):
        move = self._create_invoice("FT 2026/00125", [("TESTP005", 10, 10.0)])
        wizard = self.env["account.move.reversal"].create(
            {
                "move_ids": [(6, 0, move.ids)],
                "refund_method": "refund",
                "date_mode": "custom",
                "date": fields.Date.context_today(move),
                "company_id": self.company.id,
            }
        )
        wizard.reverse_moves()
        rec = self._create_return(
            [
                {
                    "invoice_number": "FT2026-125",
                    "prescription": "TESTP005",
                    "billed": 100.0,
                    "allowed": 0.0,
                    "allowed_taxed": 0.0,
                    "days_billed": 10.0,
                }
            ]
        )
        invoice = rec.invoice_ids
        self.assertEqual(invoice.state, "already_done")
        self.assertEqual(invoice.credit_note_move_id, wizard.new_move_ids)

    def test_cross_period_reappearance(self):
        self._standard_invoice()
        first = self._create_return(self._standard_rows(), period="202604")
        rec = self._create_return(self._standard_rows(), period="202605")
        invoice = rec.invoice_ids
        line = invoice.line_ids.filtered(lambda r: r.prescription == "TESTP001")
        self.assertEqual(
            line.previous_line_id,
            first.invoice_ids.line_ids.filtered(lambda r: r.prescription == "TESTP001"),
        )
        self.assertEqual(invoice.state, "mismatch")
        invoice.line_ids.filtered("previous_line_id").write({"resolution": "duplicate"})
        self.assertEqual(invoice.state, "awaiting_official")
        self.assertAlmostEqual(invoice.credit_estimated, 0.0)
        self.assertAlmostEqual(invoice.amount_lines_untaxed, 0.0)
        invoice.line_ids.filtered("previous_line_id").write({"resolution": "new"})
        self.assertAlmostEqual(invoice.credit_estimated, 38.16)

    def test_same_period_reappearance(self):
        # SPMS report cuts are arbitrary (a file can span months), so a claim
        # already present in another return of the same period must be flagged
        self._standard_invoice()
        first = self._create_return(self._standard_rows())
        rec = self._create_return(self._standard_rows())
        invoice = rec.invoice_ids
        line = invoice.line_ids.filtered(lambda r: r.prescription == "TESTP001")
        self.assertEqual(
            line.previous_line_id,
            first.invoice_ids.line_ids.filtered(lambda r: r.prescription == "TESTP001"),
        )
        self.assertEqual(invoice.state, "mismatch")

    def test_cross_period_link_lost_on_old_reprocess(self):
        """KNOWN LIMITATION: reprocessing the OLD return rebuilds its lines,
        so the new return's previous_line_id links are set to NULL (ondelete)
        and its pending resolutions are released without a human decision."""
        self._standard_invoice()
        first = self._create_return(self._standard_rows(), period="202604")
        rec = self._create_return(self._standard_rows(), period="202605")
        pending = rec.invoice_ids.line_ids.filtered("previous_line_id")
        self.assertTrue(pending)
        first.action_process()
        self.assertFalse(rec.invoice_ids.line_ids.mapped("previous_line_id"))

    def test_duplicate_resolution_excluded_from_generation(self):
        self._standard_invoice()
        self._create_return(self._standard_rows(), period="202604")
        rec = self._create_return(self._standard_rows(), period="202605")
        invoice = rec.invoice_ids
        duplicates = invoice.line_ids.filtered(
            lambda r: r.prescription in ("TESTP002", "TESTP003")
        )
        duplicates.write({"resolution": "duplicate"})
        invoice.line_ids.filtered(lambda r: r.prescription == "TESTP001").write(
            {"resolution": "new"}
        )
        self.assertAlmostEqual(invoice.credit_estimated, 32.86)
        invoice.write({"credit_official": 32.86})
        rec.action_create_credit_notes()
        draft = invoice.credit_note_move_id
        self.assertEqual(len(draft.invoice_line_ids), 1)
        self.assertEqual(draft.invoice_line_ids.spms_prescription, "TESTP001")
        self.assertAlmostEqual(draft.amount_total, 32.86)

    def test_cancel_and_back_to_draft(self):
        rec = self._create_return(self._standard_rows(), process=False)
        rec.action_cancel()
        self.assertEqual(rec.state, "cancel")
        rec.action_back_to_draft()
        self.assertEqual(rec.state, "draft")
