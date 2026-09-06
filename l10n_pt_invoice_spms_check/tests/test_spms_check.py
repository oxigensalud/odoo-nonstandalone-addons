# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from lxml import etree
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression
from odoo.tests.common import SavepointCase
from odoo.tools import mute_logger
from odoo.tools.safe_eval import safe_eval


class TestSpmsCheck(SavepointCase):
    """Exercise the check-result store and the credit-note generation.

    The tests build the results, their lines and their errors the way
    the parser does, then drive the generation through its business
    method; the parser and the automatic trigger have their own suites.
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
    def _create_result(
        cls, move, lines, credit_official=0.0, check_state="with_errors"
    ):
        """Build a check result the way the parser does.

        lines: list of dicts keyed like the parser output, one per claim
        (prescription), each linked to the original invoice line by
        prescription number and carrying its errors: one C010 at claim
        level unless `errors` says otherwise. The invoice-level taxed
        totals are set so the computed official credit equals
        `credit_official` exactly.
        """
        result = cls.env["spms.invoice.check"].create(
            {
                "move_id": move.id,
                "check_state": check_state,
                "fetch_date": fields.Datetime.now(),
                "total_billed": sum(line.get("billed", 0.0) for line in lines),
                "total_allowed": sum(line.get("allowed", 0.0) for line in lines),
                "total_billed_taxed": move.amount_total,
                "total_allowed_taxed": move.amount_total - credit_official,
            }
        )
        error_type_model = cls.env["spms.invoice.check.error.type"]
        line_vals_list = []
        for line in lines:
            move_lines = move.invoice_line_ids.filtered(
                lambda move_line: move_line.spms_prescription
                == line.get("prescription")
            )
            line_vals_list.append(
                {
                    "result_id": result.id,
                    "prescription": line.get("prescription"),
                    "amount_billed": line.get("billed", 0.0),
                    "amount_allowed": line.get("allowed", 0.0),
                    "days_billed": line.get("days_billed", 0.0),
                    "days_paid": line.get("days_paid", 0.0),
                    "move_line_id": (move_lines.id if len(move_lines) == 1 else False),
                    "error_ids": [
                        (
                            0,
                            0,
                            {
                                "result_id": result.id,
                                "level": error.get("level", "prestacao"),
                                "error_type_id": error_type_model._get_or_create(
                                    error.get("code", "C010")
                                ).id,
                                "description": error.get("description", "Test error"),
                                "provider_system_ref": error.get("provider_ref"),
                            },
                        )
                        for error in line.get("errors", [{}])
                    ],
                }
            )
        cls.env["spms.invoice.check.line"].create(line_vals_list)
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
        self.assertFalse(result.name)
        self.assertEqual(result.display_name, move.display_name)
        self.assertAlmostEqual(result.credit_official, 38.16)
        self.assertAlmostEqual(result.amount_lines_untaxed, 36.0)
        self.assertEqual(set(result.error_ids.mapped("code")), {"C010"})
        self.assertEqual(result.line_count, 3)
        self.assertEqual(result.error_count, 3)
        for line in result.line_ids:
            self.assertEqual(line.move_line_id.spms_prescription, line.prescription)
            self.assertEqual(line.error_ids.line_id, line)
            self.assertEqual(line.error_ids.result_id, result)

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
            result._generate_note()
        self.assertFalse(result.note_move_id)

    def test_without_errors_result_closes_as_zero(self):
        move = self._standard_invoice()
        result = self._create_result(move, [], check_state="without_errors")
        self.assertEqual(result.state, "zero_official")
        self.assertEqual(result.error_count, 0)

    def test_generate_creates_credit_note(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        draft = result._generate_note()
        self.assertTrue(draft)
        self.assertEqual(draft.state, "draft")
        self.assertEqual(draft.move_type, "out_refund")
        self.assertEqual(draft.reversed_entry_id, move)
        self.assertEqual(draft.invoice_origin, move.name)
        self.assertAlmostEqual(draft.amount_total, 38.16)
        self.assertEqual(result.note_move_id, draft)
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
        for line in result.line_ids:
            self.assertEqual(
                line.note_move_line_id,
                by_prescription[line.prescription],
            )

    def test_generate_twice_blocks(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        draft = result._generate_note()
        with self.assertRaisesRegex(UserError, "no longer ready"):
            result._generate_note()
        self.assertEqual(result.note_move_id, draft)
        self.assertEqual(result.state, "done")

    def test_generate_unmatched_prescription_blocks(self):
        move = self._standard_invoice()
        rows = self._standard_rows()
        rows[0]["prescription"] = "TESTMISSING"
        result = self._create_result(move, rows, credit_official=38.16)
        with self.assertRaisesRegex(UserError, "not matched"):
            result._generate_note()
        self.assertFalse(result.note_move_id)
        self.assertEqual(result.state, "ready")

    def test_generate_does_not_return_quantities_to_the_sale_order(self):
        # the CCF cut is definitive: what is credited is never invoiced
        # again, so the credited lines must not come back as pending on
        # the sale order they were invoiced from
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        draft = result._generate_note()
        credited = draft.invoice_line_ids.filtered("spms_prescription")
        self.assertEqual(len(credited), 3)
        self.assertFalse(any(credited.mapped("sale_qty_to_reinvoice")))

    def _sale_order_invoice(self):
        """The standard invoice, this time made from a sale order."""
        self.product.invoice_policy = "order"
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "name": prescription,
                            "product_id": self.product.id,
                            "product_uom": self.product.uom_id.id,
                            "product_uom_qty": days,
                            "price_unit": price_unit,
                            "tax_id": [(6, 0, self.tax6.ids)],
                        },
                    )
                    for prescription, days, price_unit in (
                        ("TESTP001", 31, 1.0),
                        ("TESTP002", 31, 2.0),
                        ("TESTP003", 10, 3.0),
                    )
                ],
            }
        )
        order.action_confirm()
        move = order.with_context(default_journal_id=self.journal.id)._create_invoices()
        # the sale-to-invoice propagation of the prescription number lives
        # outside this module's dependencies: the sale line description
        # carries it here and the invoice line takes it from there
        for line in move.invoice_line_ids:
            line.spms_prescription = line.sale_line_ids.name
        move.action_post()
        self.assertEqual(order.invoice_status, "invoiced")
        return order, move

    def _assert_order_fully_invoiced(self, order):
        self.assertEqual(order.invoice_status, "invoiced")
        for line in order.order_line:
            self.assertEqual(line.qty_invoiced, line.product_uom_qty)
            self.assertEqual(line.qty_to_invoice, 0)

    def test_generate_keeps_the_sale_order_fully_invoiced(self):
        # the same three line rewrites as production (total rejection,
        # partial with days, money-only fallback), this time on an invoice
        # made from a sale order: none of them may bring quantities back
        # to invoice on that order
        order, move = self._sale_order_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        draft = result._generate_note()
        self.assertEqual(len(draft.invoice_line_ids), 3)
        self._assert_order_fully_invoiced(order)

    def test_generate_credits_multi_error_line_once(self):
        # a prescription reported with several errors (e.g. C010 at
        # prescription-data level plus C012 at line level) is one claim,
        # so one line and one credit-note line: the errors carry no money
        move = self._create_invoice("FT 2026/00126", [("TESTP006", 31, 1.0)])
        lines = [
            {
                "prescription": "TESTP006",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
                "errors": [
                    {"code": "C010", "level": "prescricao"},
                    {"code": "C012", "level": "linha"},
                ],
            }
        ]
        result = self._create_result(move, lines, credit_official=32.86)
        self.assertEqual(result.line_count, 1)
        self.assertEqual(result.error_count, 2)
        self.assertAlmostEqual(result.amount_lines_untaxed, 31.0)
        self.assertEqual(result.error_ids.mapped("code"), ["C010", "C012"])
        self.assertEqual(result.line_ids.error_ids.mapped("code"), ["C010", "C012"])
        draft = result._generate_note()
        self.assertAlmostEqual(draft.amount_total, 32.86)
        self.assertEqual(len(draft.invoice_line_ids), 1)
        self.assertEqual(result.line_ids.note_move_line_id, draft.invoice_line_ids)

    def test_generate_skips_zero_difference_prescriptions(self):
        # C012-style noise where read equals computed cuts nothing: the
        # prescription stays out of the credit note
        move = self._create_invoice(
            "FT 2026/00127", [("TESTP007", 31, 1.0), ("TESTP008", 10, 1.0)]
        )
        lines = [
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
                "errors": [{"code": "C012", "level": "linha"}],
            },
        ]
        result = self._create_result(move, lines, credit_official=32.86)
        draft = result._generate_note()
        self.assertEqual(len(draft.invoice_line_ids), 1)
        self.assertEqual(draft.invoice_line_ids.spms_prescription, "TESTP007")
        zero_line = result.line_ids.filtered(
            lambda line: line.prescription == "TESTP008"
        )
        self.assertFalse(zero_line.note_move_line_id)

    def test_generate_without_difference_blocks(self):
        move = self._create_invoice("FT 2026/00128", [("TESTP009", 10, 1.0)])
        lines = [
            {
                "prescription": "TESTP009",
                "billed": 10.0,
                "allowed": 10.0,
                "errors": [{"code": "C012"}],
            }
        ]
        result = self._create_result(move, lines, credit_official=1.0)
        with self.assertRaisesRegex(UserError, "no prescription with a difference"):
            result._generate_note()

    def test_generate_official_one_cent_below_imposes_tax(self):
        # claims 36,00 + 6% global tax 2,16 = 38,16 naturally; the CCF
        # computed its tax one cent lower: the cent is written on the tax
        # line, the entry stays balanced and no extra line appears
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.15)
        draft = result._generate_note()
        self.assertAlmostEqual(draft.amount_total, 38.15)
        self.assertAlmostEqual(draft.amount_untaxed, 36.0)
        self.assertAlmostEqual(draft.amount_tax, 2.15)
        self.assertEqual(len(draft.invoice_line_ids), 3)
        tax_line = draft.line_ids.filtered(lambda line: line.tax_line_id == self.tax6)
        self.assertAlmostEqual(tax_line.debit, 2.15)
        self.assertAlmostEqual(
            sum(draft.line_ids.mapped("debit")),
            sum(draft.line_ids.mapped("credit")),
        )
        self.assertEqual(result.state, "done")

    def test_generate_official_one_cent_above_imposes_tax(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.17)
        draft = result._generate_note()
        self.assertAlmostEqual(draft.amount_total, 38.17)
        self.assertAlmostEqual(draft.amount_untaxed, 36.0)
        self.assertAlmostEqual(draft.amount_tax, 2.17)
        self.assertEqual(len(draft.invoice_line_ids), 3)
        self.assertEqual(result.state, "done")

    def _single_line_result(self, name, prescription, credit_official):
        """One-line invoice tuned for the half-cent case: base 10.75 gives
        an exact tax of 0.645, so the natural credit note totals 11.40 and
        a CCF rounding that half cent the other way states 11.39."""
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

    def test_generate_single_line_half_cent_tax(self):
        result = self._single_line_result("FT 2026/00130", "TESTP011", 11.39)
        draft = result._generate_note()
        self.assertAlmostEqual(draft.amount_total, 11.39)
        self.assertEqual(len(draft.invoice_line_ids), 1)
        tax_line = draft.line_ids.filtered(lambda line: line.tax_line_id == self.tax6)
        self.assertAlmostEqual(tax_line.debit, 0.64)
        self.assertAlmostEqual(
            sum(draft.line_ids.mapped("debit")),
            sum(draft.line_ids.mapped("credit")),
        )
        self.assertEqual(result.state, "done")

    def test_generate_needs_single_tax_line(self):
        # two tax rates on the affected lines give two tax lines: the cent
        # has no single line to land on, so the result is held for review
        tax23 = self.env["account.tax"].create(
            {
                "name": "IVA 23% test",
                "amount_type": "percent",
                "amount": 23.0,
                "type_tax_use": "sale",
                "company_id": self.company.id,
            }
        )
        move = self._create_invoice(
            "FT 2026/00137", [("TESTP030", 31, 1.0), ("TESTP031", 10, 1.0, tax23)]
        )
        rows = [
            {
                "prescription": "TESTP030",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
            },
            {
                "prescription": "TESTP031",
                "billed": 10.0,
                "allowed": 0.0,
                "days_billed": 10.0,
            },
        ]
        # claims 41,00 + 6% of 31,00 + 23% of 10,00 = 45,16 naturally
        result = self._create_result(move, rows, credit_official=45.15)
        with self.assertRaisesRegex(UserError, "exactly one tax line"):
            result._generate_note()

    def test_generate_negative_claim_gets_own_negative_line(self):
        # the check allowed TESTP021 above the billed amount: its negative
        # difference nets against the rejected TESTP020 inside the credit
        # note, one line per prescription, and no tax adjustment is needed
        move = self._create_invoice(
            "FT 2026/00140", [("TESTP020", 31, 1.0), ("TESTP021", 10, 2.0)]
        )
        lines = [
            {
                "prescription": "TESTP020",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
            },
            {
                "prescription": "TESTP021",
                "billed": 20.0,
                "allowed": 22.0,
                "days_billed": 10.0,
                "days_paid": 11.0,
                "errors": [{"code": "C012", "level": "linha"}],
            },
        ]
        # claims 31,00 - 2,00 = 29,00 + 6% global tax 1,74 = 30,74 official
        result = self._create_result(move, lines, credit_official=30.74)
        self.assertAlmostEqual(result.amount_lines_untaxed, 29.0)
        self.assertAlmostEqual(result.amount_lines_negative_untaxed, -2.0)
        draft = result._generate_note()
        self.assertEqual(len(draft.invoice_line_ids), 2)
        negative = draft.invoice_line_ids.filtered(
            lambda line: line.spms_prescription == "TESTP021"
        )
        self.assertEqual(negative.quantity, 1)
        self.assertAlmostEqual(negative.price_unit, -2.0)
        self.assertAlmostEqual(negative.price_subtotal, -2.0)
        self.assertFalse(negative.spms_start_date)
        self.assertAlmostEqual(draft.amount_untaxed, 29.0)
        self.assertAlmostEqual(draft.amount_total, 30.74)
        negative_claim = result.line_ids.filtered(
            lambda line: line.prescription == "TESTP021"
        )
        self.assertEqual(negative_claim.note_move_line_id, negative)
        self.assertEqual(result.state, "done")

    def test_generate_residual_beyond_limit_holds(self):
        # the natural credit note totals 38,16: an official value 0,06 away
        # is a discrepancy to review, not rounding noise, until the company
        # limit says otherwise
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.22)
        with self.assertRaisesRegex(
            UserError, "SPMS adjustment limit of company"
        ), self.env.cr.savepoint():
            result._generate_note()
        result._generate_note_or_hold()
        self.assertEqual(result.state, "error")
        self.assertIn("adjustment limit", result.generation_error)
        self.assertFalse(result.note_move_id)
        self.assertFalse(
            self.env["account.move"].search(
                [
                    ("move_type", "=", "out_refund"),
                    ("journal_id", "=", self.journal.id),
                ]
            )
        )
        self.company.spms_adjustment_limit = 0.10
        result.generation_error = False
        draft = result._generate_note()
        self.assertAlmostEqual(draft.amount_total, 38.22)
        self.assertAlmostEqual(draft.amount_tax, 2.22)
        self.assertEqual(len(draft.invoice_line_ids), 3)
        self.assertEqual(result.state, "done")

    def test_generate_residual_at_limit_adjusts(self):
        # exactly the limit still counts as rounding noise
        self.company.spms_adjustment_limit = 0.05
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.21)
        draft = result._generate_note()
        self.assertAlmostEqual(draft.amount_total, 38.21)
        self.assertAlmostEqual(draft.amount_tax, 2.21)
        self.assertEqual(len(draft.invoice_line_ids), 3)

    def test_company_adjustment_limit_not_negative(self):
        with self.assertRaises(IntegrityError), mute_logger(
            "odoo.sql_db"
        ), self.env.cr.savepoint():
            self.company.spms_adjustment_limit = -0.01
            self.company.flush()

    def test_generate_official_exceeds_total_blocks(self):
        # an official value above the original invoice total can only be a
        # check-data anomaly: the guard fires before the draft
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=200.0)
        self.assertEqual(result.state, "ready")
        with self.assertRaisesRegex(
            UserError, "exceeds the total of the original invoice"
        ):
            result._generate_note()
        self.assertFalse(result.note_move_id)

    def test_generate_official_equals_total_allowed(self):
        # a full rejection is legitimate: official == invoice total must
        # generate (the guard is strictly greater-than); every claim is
        # refused in full, so the lines alone reach the official value
        # and no tax adjustment is needed
        move = self._standard_invoice()
        rows = [
            {
                "prescription": "TESTP001",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
            },
            {
                "prescription": "TESTP002",
                "billed": 62.0,
                "allowed": 0.0,
                "days_billed": 31.0,
            },
            {
                "prescription": "TESTP003",
                "billed": 30.0,
                "allowed": 0.0,
                "days_billed": 10.0,
            },
        ]
        result = self._create_result(move, rows, credit_official=move.amount_total)
        result._generate_note()
        self.assertEqual(result.state, "done")
        credit_note = result.note_move_id
        self.assertEqual(len(credit_note.invoice_line_ids), 3)
        self.assertAlmostEqual(credit_note.amount_total, move.amount_total)

    def test_official_locked_while_credit_note_alive(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        result._generate_note()
        with self.assertRaisesRegex(UserError, "cancel that note"):
            result.total_allowed_taxed = move.amount_total - 40.0
        result.note_move_id.button_cancel()
        result.total_allowed_taxed = move.amount_total - 40.0
        self.assertAlmostEqual(result.credit_official, 40.0)
        self.assertEqual(result.state, "ready")

    def test_credit_note_cancel_releases_result_immediately(self):
        # the invariant is eager: cancelling the credit note frees the
        # result in the same transaction — no manual step — and leaves a
        # note in the original invoice's chatter
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        result._generate_note()
        result.note_move_id.button_cancel()
        self.assertFalse(result.note_move_id)
        self.assertEqual(result.state, "ready")
        self.assertAlmostEqual(result.credit_official, 38.16)
        self.assertTrue(
            any("was cancelled" in body for body in move.message_ids.mapped("body"))
        )

    def test_credit_note_delete_releases_result_immediately(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        result._generate_note()
        result.note_move_id.unlink()
        self.assertFalse(result.note_move_id)
        self.assertEqual(result.state, "ready")
        self.assertAlmostEqual(result.credit_official, 38.16)

    def test_credit_note_cancelled_regenerates(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        first_draft = result._generate_note()
        first_draft.button_cancel()
        self.assertEqual(result.state, "ready")
        second_draft = result._generate_note()
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
        self.assertFalse(result.note_move_id)
        self.assertIn(foreign.display_name, result.error_message)
        with self.assertRaisesRegex(UserError, "no longer ready"):
            result._generate_note()

    def test_second_credit_note_beside_own_marks_error(self):
        # one credit note per invoice (manual p.33): a second live note
        # beside our own is an error, never masked by done — and our own
        # pointer survives untouched; fixing accounting heals the result
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        own = result._generate_note()
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
        self.assertEqual(result.note_move_id, own)
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

    def _debit_invoice(self):
        """One claim the check allowed above the billed amount, alone: the
        official value comes out negative (33 computed for 31 billed, taxes
        apart), so the note must charge the 2 (plus tax) back."""
        move = self._create_invoice("FT 2026/00150", [("TESTP010", 31, 1.0)])
        rows = [
            {
                "prescription": "TESTP010",
                "billed": 31.0,
                "allowed": 33.0,
                "days_billed": 31.0,
                "days_paid": 31.0,
                "errors": [{"code": "C011"}],
            }
        ]
        return move, rows

    def _foreign_debit_note(self, move):
        wizard = (
            self.env["account.debit.note"]
            .with_context(active_model="account.move", active_ids=move.ids)
            .create({"date": fields.Date.context_today(move), "copy_lines": True})
        )
        wizard.create_debit()
        return move.debit_note_ids

    def test_generate_negative_official_creates_debit_note(self):
        move, rows = self._debit_invoice()
        result = self._create_result(move, rows, credit_official=-2.12)
        self.assertEqual(result.state, "ready")
        draft = result._generate_note()
        self.assertEqual(draft.move_type, "out_invoice")
        self.assertEqual(draft.state, "draft")
        self.assertEqual(draft.debit_origin_id, move)
        self.assertEqual(move.debit_note_ids, draft)
        self.assertFalse(move.reversal_move_id)
        self.assertEqual(len(draft.invoice_line_ids), 1)
        line = draft.invoice_line_ids
        self.assertEqual(line.spms_prescription, "TESTP010")
        self.assertEqual(line.quantity, 1)
        self.assertAlmostEqual(line.price_unit, 2.0)
        self.assertAlmostEqual(draft.amount_untaxed, 2.0)
        self.assertAlmostEqual(draft.amount_total, 2.12)
        self.assertEqual(result.state, "done")
        self.assertEqual(result.note_move_id, draft)
        self.assertEqual(result.line_ids.note_move_line_id, line)
        self.assertTrue(result.official_locked)

    def test_generate_negative_official_mixed_claims(self):
        # an over-billed claim and a larger one the check allowed above the
        # billed amount: the net is negative, so the debit note carries
        # both with their signs flipped, the same netting as the official
        # value
        move = self._create_invoice(
            "FT 2026/00151", [("TESTP010", 31, 1.0), ("TESTP011", 31, 2.0)]
        )
        rows = [
            {
                "prescription": "TESTP010",
                "billed": 31.0,
                "allowed": 29.0,
                "days_billed": 31.0,
                "days_paid": 29.0,
                "errors": [{"code": "C011"}],
            },
            {
                "prescription": "TESTP011",
                "billed": 62.0,
                "allowed": 67.0,
                "days_billed": 31.0,
                "days_paid": 31.0,
                "errors": [{"code": "C011"}],
            },
        ]
        result = self._create_result(move, rows, credit_official=-3.18)
        self.assertAlmostEqual(result.amount_lines_untaxed, -3.0)
        self.assertAlmostEqual(result.amount_lines_negative_untaxed, -5.0)
        draft = result._generate_note()
        self.assertEqual(draft.move_type, "out_invoice")
        by_prescription = {
            line.spms_prescription: line for line in draft.invoice_line_ids
        }
        self.assertEqual(set(by_prescription), {"TESTP010", "TESTP011"})
        over_billed = by_prescription["TESTP010"]
        self.assertEqual(over_billed.quantity, 2)
        self.assertAlmostEqual(over_billed.price_unit, -1.0)
        self.assertAlmostEqual(over_billed.price_subtotal, -2.0)
        allowed_above = by_prescription["TESTP011"]
        self.assertEqual(allowed_above.quantity, 1)
        self.assertAlmostEqual(allowed_above.price_unit, 5.0)
        self.assertAlmostEqual(draft.amount_untaxed, 3.0)
        self.assertAlmostEqual(draft.amount_total, 3.18)
        self.assertEqual(result.state, "done")

    def test_generate_negative_official_one_cent_imposes_tax(self):
        # the mirror of the credit-note cent: written on the tax line, on
        # its credit side this time, and compensated on the receivable line
        move, rows = self._debit_invoice()
        result = self._create_result(move, rows, credit_official=-2.13)
        draft = result._generate_note()
        self.assertAlmostEqual(draft.amount_untaxed, 2.0)
        self.assertAlmostEqual(draft.amount_tax, 0.13)
        self.assertAlmostEqual(draft.amount_total, 2.13)
        tax_line = draft.line_ids.filtered("tax_line_id")
        self.assertEqual(len(tax_line), 1)
        self.assertAlmostEqual(tax_line.credit, 0.13)
        self.assertAlmostEqual(tax_line.debit, 0.0)
        self.assertAlmostEqual(
            sum(draft.line_ids.mapped("debit")), sum(draft.line_ids.mapped("credit"))
        )
        self.assertEqual(result.state, "done")

    def test_generate_negative_official_keeps_the_sale_order_fully_invoiced(self):
        # the debit-note flow copies the sale-order link onto the lines;
        # kept, the debit line would count as invoiced again on the order
        order, move = self._sale_order_invoice()
        rows = [
            {
                "prescription": "TESTP001",
                "billed": 31.0,
                "allowed": 33.0,
                "days_billed": 31.0,
                "days_paid": 31.0,
                "errors": [{"code": "C011"}],
            }
        ]
        result = self._create_result(move, rows, credit_official=-2.12)
        draft = result._generate_note()
        self.assertEqual(draft.move_type, "out_invoice")
        self.assertEqual(len(draft.invoice_line_ids), 1)
        self.assertFalse(draft.invoice_line_ids.sale_line_ids)
        self._assert_order_fully_invoiced(order)

    def test_preexisting_debit_note_marks_error(self):
        # the same rule as for credit notes, on the debit side: a live debit
        # note of the invoice this module did not create is an error
        move, rows = self._debit_invoice()
        foreign = self._foreign_debit_note(move)
        self.assertEqual(len(foreign), 1)
        result = self._create_result(move, rows, credit_official=-2.12)
        self.assertEqual(result.state, "error")
        self.assertFalse(result.note_move_id)
        self.assertIn(foreign.display_name, result.error_message)
        self.assertIn("debit note", result.error_message)
        with self.assertRaisesRegex(UserError, "no longer ready"):
            result._generate_note()
        foreign.button_cancel()
        result._update_state()
        self.assertEqual(result.state, "ready")

    def test_foreign_note_of_the_other_kind_does_not_block(self):
        # only the kind the official value calls for counts: a debit note
        # billing a prescription late never blocks the credit note of the
        # check, and a credit note never blocks a debit result
        move = self._standard_invoice()
        self._foreign_debit_note(move)
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        self.assertEqual(result.state, "ready")
        debit_move, rows = self._debit_invoice()
        wizard = self.env["account.move.reversal"].create(
            {
                "move_ids": [(6, 0, debit_move.ids)],
                "refund_method": "refund",
                "date_mode": "custom",
                "date": fields.Date.context_today(debit_move),
                "company_id": self.company.id,
            }
        )
        wizard.reverse_moves()
        self.assertTrue(debit_move.reversal_move_id)
        result = self._create_result(debit_move, rows, credit_official=-2.12)
        self.assertEqual(result.state, "ready")

    def test_debit_note_cancel_releases_result(self):
        move, rows = self._debit_invoice()
        result = self._create_result(move, rows, credit_official=-2.12)
        draft = result._generate_note()
        with self.assertRaisesRegex(UserError, "cancel that note"):
            result.total_allowed_taxed = move.amount_total + 3.0
        draft.button_cancel()
        self.assertFalse(result.note_move_id)
        self.assertEqual(result.state, "ready")
        self.assertAlmostEqual(result.credit_official, -2.12)
        self.assertTrue(
            any("was cancelled" in body for body in move.message_ids.mapped("body"))
        )

    def test_error_autocreates_unknown_type(self):
        move = self._create_invoice("FT 2026/00132", [("TESTP013", 10, 1.0)])
        result = self._create_result(
            move,
            [
                {
                    "prescription": "TESTP013",
                    "billed": 10.0,
                    "allowed": 0.0,
                    "days_billed": 10.0,
                    "errors": [{"code": "Z999"}],
                }
            ],
        )
        error = result.error_ids
        self.assertEqual(error.code, "Z999")
        self.assertTrue(error.error_type_id)

    def _single_claim_result(self, name, prescription):
        move = self._create_invoice(name, [(prescription, 10, 1.0)])
        return self._create_result(
            move,
            [
                {
                    "prescription": prescription,
                    "billed": 10.0,
                    "allowed": 0.0,
                    "days_billed": 10.0,
                }
            ],
        )

    def test_error_under_line_of_another_check_blocks(self):
        result = self._single_claim_result("FT 2026/00133", "TESTP014")
        other = self._single_claim_result("FT 2026/00134", "TESTP015")
        with self.assertRaises(ValidationError):
            result.error_ids.line_id = other.line_ids

    def test_document_error_with_line_blocks(self):
        result = self._single_claim_result("FT 2026/00135", "TESTP016")
        with self.assertRaises(ValidationError):
            self.env["spms.invoice.check.error"].create(
                {
                    "result_id": result.id,
                    "line_id": result.line_ids.id,
                    "level": "invoice",
                    "error_type_id": result.error_ids.error_type_id.id,
                }
            )

    def test_claim_error_without_line_blocks(self):
        result = self._single_claim_result("FT 2026/00136", "TESTP017")
        with self.assertRaises(ValidationError):
            self.env["spms.invoice.check.error"].create(
                {
                    "result_id": result.id,
                    "level": "prestacao",
                    "error_type_id": result.error_ids.error_type_id.id,
                }
            )

    def test_error_list_defaults_to_the_errors_that_carry_money(self):
        """Customers > SPMS > Errors: the flat list selects by default the
        errors of the claims the check cut or raised plus the errors
        anchored to the document itself, and leaves the informational
        errors of untouched claims to the *Without Difference* filter."""
        move = self._create_invoice(
            "FT 2026/00160", [("TESTP020", 31, 1.0), ("TESTP021", 31, 2.0)]
        )
        result = self._create_result(
            move,
            [
                {"prescription": "TESTP020", "billed": 31.0, "allowed": 0.0},
                {
                    "prescription": "TESTP021",
                    "billed": 62.0,
                    "allowed": 62.0,
                    "errors": [{"code": "C012", "level": "linha"}],
                },
            ],
        )
        document_error = self.env["spms.invoice.check.error"].create(
            {
                "result_id": result.id,
                "level": "invoice",
                "error_type_id": self.env["spms.invoice.check.error.type"]
                ._get_or_create("C313")
                .id,
                "description": "Test document error",
            }
        )
        lines = {line.prescription: line for line in result.line_ids}
        cut_error = lines["TESTP020"].error_ids
        noise_error = lines["TESTP021"].error_ids
        self.assertEqual(cut_error.move_id, move)
        self.assertEqual(cut_error.partner_id, move.partner_id)
        self.assertEqual(cut_error.amount_difference, 31.0)
        self.assertFalse(document_error.amount_difference)
        menu = self.env.ref("l10n_pt_invoice_spms_check.spms_invoice_check_error_menu")
        action = self.env.ref(
            "l10n_pt_invoice_spms_check.spms_invoice_check_error_action"
        )
        self.assertEqual(menu.action, action)
        self.assertEqual(
            safe_eval(action.context),
            {
                "search_default_filter_with_difference": 1,
                "search_default_filter_document": 1,
            },
        )
        arch = etree.fromstring(
            self.env["spms.invoice.check.error"].fields_view_get(view_type="search")[
                "arch"
            ]
        )
        domains = {
            node.get("name"): safe_eval(node.get("domain"))
            for node in arch.iter("filter")
            if node.get("domain")
        }
        errors = self.env["spms.invoice.check.error"]
        of_result = [("result_id", "=", result.id)]
        self.assertEqual(
            errors.search(of_result + domains["filter_with_difference"]), cut_error
        )
        self.assertEqual(
            errors.search(of_result + domains["filter_document"]), document_error
        )
        self.assertEqual(
            errors.search(of_result + domains["filter_without_difference"]),
            noise_error,
        )
        # the two defaults share a filter group, which the client joins with OR
        self.assertEqual(
            errors.search(
                of_result
                + expression.OR(
                    [domains["filter_with_difference"], domains["filter_document"]]
                )
            ),
            cut_error | document_error,
        )
