# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from collections import Counter
from datetime import date
from unittest import mock

from odoo.addons.component.tests.common import SavepointComponentCase
from odoo.addons.queue_job.tests.common import trap_jobs

from .test_spms_check_process import (
    _document,
    _erro,
    _linha,
    _linha_prescricao,
    _prescricao,
    _prestacao,
)
from .test_spms_check_transport import TRANSPORT_PATH, _FakeTransport, _result_response

# One wire-faithful journey: the answer envelope carries the check
# document base64-wrapped in 76-column lines exactly like the live
# service, and the document mirrors the SHAPE of the most complex real
# conference document observed to date (105 errors on 96 claims, the 7
# known codes at once, anchored only at claim and line level — no real
# document anchors errors anywhere else). Every value is synthetic.

CREDIT_OFFICIAL = 480.0
CLAIMS = 96
CODE_MIX_LINHA = ["C012"] * 90 + ["C010"] * 4 + ["C013"] * 2
CODE_MIX_PRESTACAO = ["C011"] * 4 + ["A004"] * 3 + ["D171"] + ["D306"]


def _complex_document():
    claims = []
    for index in range(CLAIMS):
        prescription = "TESTP%03d" % (index + 1)
        line_errors = _erro(CODE_MIX_LINHA[index])
        claim_errors = (
            _erro(CODE_MIX_PRESTACAO[index]) if index < len(CODE_MIX_PRESTACAO) else ""
        )
        claims.append(
            _prestacao(
                prescription,
                billed="6.00",
                allowed="1.00",
                errors=claim_errors,
                lines=_linha("REF%03d" % (index + 1), line_errors),
            )
        )
    return _document(
        # tax-free lines: the official cut is the claims total exactly
        total_billed="2976.00",
        total_allowed="2496.00",
        total_billed_taxed="2976.00",
        total_allowed_taxed="2496.00",
        claims="".join(claims),
    )


def _wire_wrap(document):
    encoded = base64.b64encode(document.encode()).decode()
    wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    return _result_response("<documento>%s</documento>" % wrapped)


class TestSpmsCheckIntegration(SavepointComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.tax_calculation_rounding_method = "round_globally"
        cls.company.vat = "PT999999990"
        cls.company.spms_username = "test-user"
        cls.company.spms_password = "test-secret"
        cls.backend = cls.env.ref("l10n_pt_invoice_spms.spms_backend")
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
            "name": "SPMS Integration Test Sales",
            "code": "TSPI",
            "type": "sale",
            "company_id": cls.company.id,
            "default_account_id": cls.income_account.id,
        }
        if "edi_format_ids" in cls.env["account.journal"]._fields:
            journal_vals["edi_format_ids"] = [(5, 0, 0)]
        cls.journal = cls.env["account.journal"].create(journal_vals)
        cls.partner = cls.env["res.partner"].create(
            {"name": "Integration Partner", "spms_assigned_id": "12345678"}
        )
        cls.invoice = cls.env["account.move"].create(
            {
                "name": "FT TEST/00001",
                "move_type": "out_invoice",
                "partner_id": cls.partner.id,
                "journal_id": cls.journal.id,
                "invoice_date": date(2026, 5, 31),
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "SPMS service %03d" % (index + 1),
                            "quantity": 31,
                            "price_unit": 1.0,
                            "account_id": cls.income_account.id,
                            "tax_ids": [(5, 0, 0)],
                            "spms_prescription": "TESTP%03d" % (index + 1),
                        },
                    )
                    for index in range(CLAIMS)
                ],
            }
        )
        cls.invoice.action_post()
        cls.exchange = cls.backend.create_record(
            "l10n_pt_spms",
            {
                "edi_exchange_state": "output_sent_and_processed",
                "model": "account.move",
                "res_id": cls.invoice.id,
            },
        )

    def test_full_journey_to_draft_credit_note(self):
        document = _complex_document()
        transport = _FakeTransport(_wire_wrap(document))
        with mock.patch(TRANSPORT_PATH, return_value=transport), trap_jobs() as trap:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
            trap.perform_enqueued_jobs()

        child = self.env["edi.exchange.record"].search(
            [
                ("type_id.code", "=", "l10n_pt_spms_check"),
                ("model", "=", "account.move"),
                ("res_id", "=", self.invoice.id),
            ]
        )
        self.assertEqual(len(child), 1)
        self.assertEqual(child.edi_exchange_state, "input_processed")

        result = self.env["spms.invoice.check"].search(
            [("move_id", "=", self.invoice.id)]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result.check_state, "with_errors")
        self.assertAlmostEqual(result.credit_official, CREDIT_OFFICIAL, places=2)

        lines = result.line_ids
        self.assertEqual(len(lines), CLAIMS)
        self.assertAlmostEqual(sum(lines.mapped("amount_difference")), 480.0)
        self.assertAlmostEqual(result.amount_lines_untaxed, 480.0)
        rows = result.error_ids
        self.assertEqual(len(rows), 105)
        self.assertEqual(lines.error_ids, rows)
        self.assertFalse(result.document_error_ids)
        self.assertEqual(
            Counter(rows.mapped("level")), Counter({"linha": 96, "prestacao": 9})
        )
        self.assertEqual(
            Counter(rows.mapped("code")),
            Counter(
                {
                    "C012": 90,
                    "C010": 4,
                    "C011": 4,
                    "A004": 3,
                    "C013": 2,
                    "D171": 1,
                    "D306": 1,
                }
            ),
        )

        # the stored document survives the base64 unwrapping verbatim
        attachment = self.env["ir.attachment"].search(
            [("res_model", "=", "spms.invoice.check"), ("res_id", "=", result.id)]
        )
        self.assertEqual(len(attachment), 1)
        self.assertEqual(base64.b64decode(attachment.datas).decode(), document)

        # official credit by construction: draft credit note, exact total
        self.assertFalse(result.generation_error)
        self.assertEqual(result.state, "done")
        credit_note = result.note_move_id
        self.assertTrue(credit_note)
        self.assertEqual(credit_note.state, "draft")
        self.assertEqual(credit_note.move_type, "out_refund")
        self.assertAlmostEqual(credit_note.amount_total, CREDIT_OFFICIAL, places=2)
        self.assertEqual(len(credit_note.invoice_line_ids), CLAIMS)

    def test_full_journey_all_anchor_levels(self):
        # the schema anchors errors at five places (invoice, claim, claim
        # line, prescription data and its lines); no real document has
        # used all five at once yet, but the wire allows it and the
        # module must place every row where it belongs. The unknown
        # code must auto-create its catalogue entry.
        document = _document(
            total_billed="100.00",
            total_allowed="100.00",
            total_billed_taxed="100.00",
            total_allowed_taxed="100.00",
            invoice_errors=_erro("Z999"),
            claims=_prestacao(
                "TESTP001",
                errors=_erro("C011"),
                lines=_linha("REF001", _erro("C012")),
                prescription_data=_prescricao(
                    _linha_prescricao("REF002", _erro("D306")) + _erro("C010")
                ),
            ),
        )
        transport = _FakeTransport(_wire_wrap(document))
        with mock.patch(TRANSPORT_PATH, return_value=transport), trap_jobs() as trap:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
            trap.perform_enqueued_jobs()

        child = self.env["edi.exchange.record"].search(
            [
                ("type_id.code", "=", "l10n_pt_spms_check"),
                ("model", "=", "account.move"),
                ("res_id", "=", self.invoice.id),
            ]
        )
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self.env["spms.invoice.check"].search(
            [("move_id", "=", self.invoice.id)]
        )
        rows = result.error_ids
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            Counter(rows.mapped("level")),
            Counter({"invoice": 1, "prestacao": 1, "linha": 1, "prescricao": 2}),
        )
        # the four claim-anchored errors hang from the single line, the
        # invoice one from the result itself
        self.assertEqual(len(result.line_ids), 1)
        self.assertEqual(
            Counter(result.line_ids.error_ids.mapped("level")),
            Counter({"prestacao": 1, "linha": 1, "prescricao": 2}),
        )
        self.assertEqual(
            Counter(result.document_error_ids.mapped("level")),
            Counter({"invoice": 1}),
        )
        # the unknown code arrived: catalogue entry auto-created
        # carrying the official message straight from the wire
        unknown = rows.filtered(lambda r: r.code == "Z999").error_type_id
        self.assertEqual(unknown.description, "Synthetic message")
        # equal totals: official credit zero, no credit note to draft
        self.assertEqual(result.check_state, "with_errors")
        self.assertEqual(result.state, "zero_official")
        self.assertFalse(result.note_move_id)
