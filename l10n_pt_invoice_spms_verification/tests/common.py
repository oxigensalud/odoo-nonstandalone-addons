# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo import fields
from odoo.tests.common import SavepointCase


class SpmsVerificationCase(SavepointCase):
    """Invoices and verification results built the way the parser does."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.country_id = cls.env.ref("base.pt")
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
    def _create_invoice(cls, name, lines, discount=0.0):
        """Post an SPMS-like customer invoice.

        lines: list of (prescription, days, price_unit[, tax]) tuples; every
        line carries the SPMS period dates so the total-rejection case can
        assert they are preserved on the refund. `discount` (a percentage)
        goes on every line.
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
                            "discount": discount,
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
        cls, move, lines, credit_official=0.0, verification_state="with_errors"
    ):
        """Build a verification result the way the parser does.

        lines: list of dicts keyed like the parser output, one per claim
        (prescription), each paired with its original invoice line the way
        the parser does (the line model's rule) and carrying its errors:
        one C010 at claim level unless `errors` says otherwise. The
        invoice-level taxed totals are set so the computed official credit
        equals `credit_official` exactly.
        """
        result = cls.env["spms.invoice.verification"].create(
            {
                "move_id": move.id,
                "verification_state": verification_state,
                "fetch_date": fields.Datetime.now(),
                "total_billed": sum(line.get("billed", 0.0) for line in lines),
                "total_allowed": sum(line.get("allowed", 0.0) for line in lines),
                "total_billed_taxed": move.amount_total,
                "total_allowed_taxed": move.amount_total - credit_official,
            }
        )
        error_type_model = cls.env["spms.invoice.verification.error.type"]
        originals = cls.env["spms.invoice.verification.line"]._match_original_lines(
            move,
            [
                (
                    line.get("prescription"),
                    line.get("days_billed", 0.0),
                    line.get("billed", 0.0),
                )
                for line in lines
            ],
        )
        line_vals_list = []
        for line, original in zip(lines, originals):
            line_vals_list.append(
                {
                    "result_id": result.id,
                    "prescription": line.get("prescription"),
                    "amount_billed": line.get("billed", 0.0),
                    "amount_allowed": line.get("allowed", 0.0),
                    "days_billed": line.get("days_billed", 0.0),
                    "days_paid": line.get("days_paid", 0.0),
                    "move_line_id": original.id,
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
        cls.env["spms.invoice.verification.line"].create(line_vals_list)
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

    def _debit_invoice(self):
        """One claim the verification allowed above the billed amount, alone: the
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
