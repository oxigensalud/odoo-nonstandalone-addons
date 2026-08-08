# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import SavepointCase
from odoo.tools import mute_logger


class TestSpmsCheck(SavepointCase):
    """Exercise the check-result store and the credit-note generation.

    The web-service transport and the check-document parser are not
    wired yet, so the tests build the results and their error rows the way
    the parser will; generation is then driven through its business method,
    the deepest public entry point available until the automatic trigger
    lands.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
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
    def _create_result(cls, move, rows, credit_official=0.0, check_state="with_errors"):
        """Build a check result the way the parser will.

        rows: list of dicts keyed like the future parser output; the claim
        totals are identical on every row of the same prescription and each
        row is linked to the original invoice line by prescription number.
        The invoice-level taxed totals are set so the computed official
        credit equals `credit_official` exactly.
        """
        result = cls.env["spms.invoice.check"].create(
            {
                "move_id": move.id,
                "check_state": check_state,
                "fetch_date": fields.Datetime.now(),
                "total_billed": sum(row.get("billed", 0.0) for row in rows),
                "total_allowed": sum(row.get("allowed", 0.0) for row in rows),
                "total_billed_taxed": move.amount_total,
                "total_allowed_taxed": move.amount_total - credit_official,
            }
        )
        error_vals_list = []
        for row in rows:
            move_lines = move.invoice_line_ids.filtered(
                lambda line: line.spms_prescription == row.get("prescription")
            )
            error_vals_list.append(
                {
                    "result_id": result.id,
                    "level": row.get("level", "prestacao"),
                    "error_type_id": cls.env["spms.error.type"]
                    ._get_or_create(row.get("code", "C010"))
                    .id,
                    "description": row.get("description", "Test error"),
                    "prescription": row.get("prescription"),
                    "amount_billed": row.get("billed", 0.0),
                    "amount_allowed": row.get("allowed", 0.0),
                    "days_billed": row.get("days_billed", 0.0),
                    "days_paid": row.get("days_paid", 0.0),
                    "provider_system_ref": row.get("provider_ref"),
                    "move_line_id": (move_lines.id if len(move_lines) == 1 else False),
                }
            )
        cls.env["spms.invoice.check.error"].create(error_vals_list)
        return result

    @classmethod
    def _standard_rows(cls):
        """Three prescriptions of FT 2026/00123 covering the §5 line cases.

        P1: total rejection (V=0)          -> diff 31,00 (case 1)
        P2: partial with reliable days     -> diff 4,00 (case 2, 2 days)
        P3: partial without paid days      -> diff 1,00 (case 3 fallback)
        Claims total 36,00 + 6% global tax 2,16 = 38,16 official.
        """
        return [
            {
                "prescription": "TESTP001",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
            },
            {
                "prescription": "TESTP002",
                "billed": 62.0,
                "allowed": 58.0,
                "days_billed": 31.0,
                "days_paid": 29.0,
            },
            {
                "prescription": "TESTP003",
                "billed": 30.0,
                "allowed": 29.0,
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

    def test_result_state_and_amounts_on_create(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        self.assertEqual(result.state, "ready")
        self.assertAlmostEqual(result.credit_official, 38.16)
        self.assertAlmostEqual(result.amount_lines_untaxed, 36.0)
        self.assertEqual(result.error_codes, "C010")
        self.assertEqual(result.error_count, 3)
        for row in result.error_ids:
            self.assertEqual(row.move_line_id.spms_prescription, row.prescription)

    def test_result_move_unique(self):
        move = self._standard_invoice()
        self._create_result(move, self._standard_rows(), credit_official=38.16)
        with self.assertRaises(IntegrityError), mute_logger(
            "odoo.sql_db"
        ), self.env.cr.savepoint():
            self._create_result(move, [])

    def test_zero_official_closes_result(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows())
        self.assertEqual(result.state, "zero_official")
        with self.assertRaisesRegex(UserError, "no longer ready"):
            result._generate_credit_note()
        self.assertFalse(result.credit_note_move_id)

    def test_without_errors_result_closes_as_zero(self):
        move = self._standard_invoice()
        result = self._create_result(move, [], check_state="without_errors")
        self.assertEqual(result.state, "zero_official")
        self.assertEqual(result.error_count, 0)

    def test_generate_creates_credit_note(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        draft = result._generate_credit_note()
        self.assertTrue(draft)
        self.assertEqual(draft.state, "draft")
        self.assertEqual(draft.move_type, "out_refund")
        self.assertEqual(draft.reversed_entry_id, move)
        self.assertEqual(draft.invoice_origin, move.name)
        self.assertAlmostEqual(draft.amount_total, 38.16)
        self.assertEqual(result.credit_note_move_id, draft)
        self.assertEqual(result.state, "done")
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
        for row in result.error_ids:
            self.assertEqual(
                row.refund_move_line_id,
                by_prescription[row.prescription],
            )

    def test_generate_twice_blocks(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        draft = result._generate_credit_note()
        with self.assertRaisesRegex(UserError, "no longer ready"):
            result._generate_credit_note()
        self.assertEqual(result.credit_note_move_id, draft)
        self.assertEqual(result.state, "done")

    def test_generate_unmatched_prescription_blocks(self):
        move = self._standard_invoice()
        rows = self._standard_rows()
        rows[0]["prescription"] = "TESTMISSING"
        result = self._create_result(move, rows, credit_official=38.16)
        with self.assertRaisesRegex(UserError, "not matched"):
            result._generate_credit_note()
        self.assertFalse(result.credit_note_move_id)
        self.assertEqual(result.state, "ready")

    def test_generate_dedupes_multi_error_rows(self):
        # a prescription reported by several error rows (e.g. C010 at
        # prescription-data level plus C012 at line level) is credited once:
        # every row carries the same claim totals by construction
        move = self._create_invoice("FT 2026/00126", [("TESTP006", 31, 1.0)])
        rows = [
            {
                "prescription": "TESTP006",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
                "code": "C010",
                "level": "prescricao",
            },
            {
                "prescription": "TESTP006",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
                "code": "C012",
                "level": "linha",
            },
        ]
        result = self._create_result(move, rows, credit_official=32.86)
        self.assertAlmostEqual(result.amount_lines_untaxed, 31.0)
        self.assertEqual(result.error_codes, "C010 / C012")
        draft = result._generate_credit_note()
        self.assertAlmostEqual(draft.amount_total, 32.86)
        self.assertEqual(len(draft.invoice_line_ids), 1)
        self.assertEqual(
            result.error_ids.mapped("refund_move_line_id"),
            draft.invoice_line_ids,
        )

    def test_generate_skips_zero_difference_prescriptions(self):
        # C012-style noise where read equals computed cuts nothing: the
        # prescription stays out of the credit note
        move = self._create_invoice(
            "FT 2026/00127", [("TESTP007", 31, 1.0), ("TESTP008", 10, 1.0)]
        )
        rows = [
            {
                "prescription": "TESTP007",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
            },
            {
                "prescription": "TESTP008",
                "billed": 10.0,
                "allowed": 10.0,
                "code": "C012",
                "level": "linha",
            },
        ]
        result = self._create_result(move, rows, credit_official=32.86)
        draft = result._generate_credit_note()
        self.assertEqual(len(draft.invoice_line_ids), 1)
        self.assertEqual(draft.invoice_line_ids.spms_prescription, "TESTP007")
        zero_row = result.error_ids.filtered(lambda row: row.prescription == "TESTP008")
        self.assertFalse(zero_row.refund_move_line_id)

    def test_generate_without_positive_difference_blocks(self):
        move = self._create_invoice("FT 2026/00128", [("TESTP009", 10, 1.0)])
        rows = [
            {
                "prescription": "TESTP009",
                "billed": 10.0,
                "allowed": 10.0,
                "code": "C012",
            }
        ]
        result = self._create_result(move, rows, credit_official=1.0)
        with self.assertRaisesRegex(UserError, "positive difference"):
            result._generate_credit_note()

    def test_generate_adjustment_requires_product(self):
        # the generation contract expects the caller to run each result in
        # its own savepoint (as the batch action did and the automatic
        # trigger will): a raise then leaves no half-built draft behind
        self.company.spms_adjustment_product_id = False
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.15)
        with self.assertRaisesRegex(
            UserError, "Configure the SPMS adjustment line product"
        ), self.env.cr.savepoint():
            result._generate_credit_note()
        self.assertFalse(result.credit_note_move_id)
        self.assertEqual(result.state, "ready")
        self.assertFalse(
            self.env["account.move"].search(
                [
                    ("move_type", "=", "out_refund"),
                    ("journal_id", "=", self.journal.id),
                ]
            )
        )

    def test_generate_adjustment_line_direct_base(self):
        self.company.spms_adjustment_product_id = self.adjustment_product
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.15)
        draft = result._generate_credit_note()
        self.assertAlmostEqual(draft.amount_total, 38.15)
        adjustment = draft.invoice_line_ids.filtered(
            lambda line: line.product_id == self.adjustment_product
        )
        self.assertEqual(len(adjustment), 1)
        self.assertAlmostEqual(adjustment.price_unit, -0.01)
        self.assertEqual(adjustment.tax_ids, self.tax6)
        self.assertEqual(len(draft.invoice_line_ids), 4)
        self.assertEqual(result.state, "done")

    def _adjustment_single_line_result(self, name, prescription, credit_official):
        """One-line invoice tuned for cent-edge cases: base 10.75 gives an
        exact tax of 0.645, so the natural credit note totals 11.40 and the
        totals reachable by moving the base jump from 11.38 to 11.40."""
        move = self._create_invoice(name, [(prescription, 1, 10.75)])
        return self._create_result(
            move,
            [
                {
                    "prescription": prescription,
                    "billed": 10.75,
                    "allowed": 0.0,
                    "days_billed": 1.0,
                }
            ],
            credit_official=credit_official,
        )

    def test_generate_adjustment_candidate_loop(self):
        self.company.spms_adjustment_product_id = self.adjustment_product
        result = self._adjustment_single_line_result("FT 2026/00129", "TESTP010", 11.38)
        draft = result._generate_credit_note()
        self.assertAlmostEqual(draft.amount_total, 11.38)
        adjustment = draft.invoice_line_ids.filtered(
            lambda line: line.product_id == self.adjustment_product
        )
        # the theoretical base -0.02 yields 11.37: the candidate loop must
        # land on -0.01
        self.assertAlmostEqual(adjustment.price_unit, -0.01)
        self.assertEqual(result.state, "done")

    def test_generate_forced_tax_amount(self):
        self.company.spms_adjustment_product_id = self.adjustment_product
        result = self._adjustment_single_line_result("FT 2026/00130", "TESTP011", 11.39)
        draft = result._generate_credit_note()
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
        self.assertEqual(result.state, "done")

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
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.15)
        with self.assertRaisesRegex(UserError, "exactly one percentage customer tax"):
            result._generate_credit_note()

    def test_generate_official_exceeds_total_blocks(self):
        # an official value above the original invoice total can only be a
        # check-data anomaly: the guard fires before the draft, even
        # with the adjustment product configured
        self.company.spms_adjustment_product_id = self.adjustment_product
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=200.0)
        self.assertEqual(result.state, "ready")
        with self.assertRaisesRegex(
            UserError, "exceeds the total of the original invoice"
        ):
            result._generate_credit_note()
        self.assertFalse(result.credit_note_move_id)

    def test_generate_official_equals_total_allowed(self):
        # a full rejection is legitimate: official == invoice total must
        # generate (the guard is strictly greater-than)
        self.company.spms_adjustment_product_id = self.adjustment_product
        move = self._standard_invoice()
        result = self._create_result(
            move, self._standard_rows(), credit_official=move.amount_total
        )
        result._generate_credit_note()
        self.assertEqual(result.state, "done")
        self.assertAlmostEqual(
            result.credit_note_move_id.amount_total, move.amount_total
        )

    def test_official_locked_while_credit_note_alive(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        result._generate_credit_note()
        with self.assertRaisesRegex(UserError, "cancel that credit note"):
            result.total_allowed_taxed = move.amount_total - 40.0
        result.credit_note_move_id.button_cancel()
        result.total_allowed_taxed = move.amount_total - 40.0
        self.assertAlmostEqual(result.credit_official, 40.0)
        self.assertEqual(result.state, "ready")

    def test_credit_note_cancel_releases_result_immediately(self):
        # the invariant is eager: cancelling the credit note frees the
        # result in the same transaction — no manual step — and leaves a
        # note in the original invoice's chatter
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        result._generate_credit_note()
        result.credit_note_move_id.button_cancel()
        self.assertFalse(result.credit_note_move_id)
        self.assertEqual(result.state, "ready")
        self.assertAlmostEqual(result.credit_official, 38.16)
        self.assertTrue(
            any("was cancelled" in body for body in move.message_ids.mapped("body"))
        )

    def test_credit_note_delete_releases_result_immediately(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        result._generate_credit_note()
        result.credit_note_move_id.unlink()
        self.assertFalse(result.credit_note_move_id)
        self.assertEqual(result.state, "ready")
        self.assertAlmostEqual(result.credit_official, 38.16)

    def test_credit_note_cancelled_regenerates(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        first_draft = result._generate_credit_note()
        first_draft.button_cancel()
        self.assertEqual(result.state, "ready")
        second_draft = result._generate_credit_note()
        self.assertEqual(result.state, "done")
        self.assertNotEqual(second_draft, first_draft)
        self.assertEqual(second_draft.state, "draft")
        self.assertEqual(first_draft.state, "cancel")

    def test_preexisting_credit_note_marks_error(self):
        # a foreign note is never adopted even when its amounts match the
        # official exactly: plain error naming the note, a human decides
        move = self._create_invoice("FT 2026/00131", [("TESTP012", 10, 10.0)])
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
        foreign = move.reversal_move_id
        result = self._create_result(
            move,
            [
                {
                    "prescription": "TESTP012",
                    "billed": 100.0,
                    "allowed": 0.0,
                    "days_billed": 10.0,
                }
            ],
            credit_official=move.amount_total,
        )
        self.assertEqual(result.state, "error")
        self.assertFalse(result.credit_note_move_id)
        self.assertIn(foreign.display_name, result.error_message)
        with self.assertRaisesRegex(UserError, "no longer ready"):
            result._generate_credit_note()

    def test_second_credit_note_beside_own_marks_error(self):
        # one credit note per invoice (manual p.33): a second live note
        # beside our own is an error, never masked by done — and our own
        # pointer survives untouched; fixing accounting heals the result
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        own = result._generate_credit_note()
        wizard = self.env["account.move.reversal"].create(
            {
                "move_ids": [(6, 0, move.ids)],
                "refund_method": "refund",
                "date_mode": "custom",
                "date": fields.Date.context_today(result),
                "company_id": self.company.id,
            }
        )
        wizard.reverse_moves()
        result._update_state()
        self.assertEqual(result.state, "error")
        self.assertEqual(result.credit_note_move_id, own)
        foreign = move.reversal_move_id - own
        self.assertIn(foreign.display_name, result.error_message)
        foreign.button_cancel()
        result._update_state()
        self.assertEqual(result.state, "done")
        self.assertFalse(result.error_message)

    def test_non_refund_reversal_does_not_block(self):
        # the foreign-note guard only counts customer credit notes: a
        # journal entry hand-linked as a reversal must not block the result
        move = self._standard_invoice()
        misc_journal = self.env["account.journal"].create(
            {
                "name": "Miscellaneous",
                "code": "TMISC",
                "type": "general",
                "company_id": self.company.id,
            }
        )
        self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": misc_journal.id,
                "reversed_entry_id": move.id,
            }
        )
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        self.assertEqual(result.state, "ready")

    def test_error_row_autocreates_unknown_type(self):
        move = self._create_invoice("FT 2026/00132", [("TESTP013", 10, 1.0)])
        result = self._create_result(
            move,
            [
                {
                    "prescription": "TESTP013",
                    "billed": 10.0,
                    "allowed": 0.0,
                    "days_billed": 10.0,
                    "code": "Z999",
                }
            ],
        )
        row = result.error_ids
        self.assertEqual(row.code, "Z999")
        self.assertTrue(row.error_type_id)
