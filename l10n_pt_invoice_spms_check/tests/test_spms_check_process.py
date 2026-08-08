# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from datetime import date

from odoo import fields

from odoo.addons.component.tests.common import SavepointComponentCase

# Synthetic CCF document shapes; the synthetic NumeroUtente must never
# land anywhere.


def _erro(code, message="Synthetic message"):
    return "<Erro><Codigo>%s</Codigo><Mensagem>%s</Mensagem></Erro>" % (code, message)


def _linha(provider_ref, errors):
    return (
        "<LinhaPrestacaoErrosEDiferencas>"
        "<SistemaPrescrito>%s</SistemaPrescrito>"
        "<QuantidadeLida>31</QuantidadeLida>"
        "<QuantidadeCalculado>0</QuantidadeCalculado>"
        "%s"
        "</LinhaPrestacaoErrosEDiferencas>" % (provider_ref, errors)
    )


def _prescricao(content):
    return (
        "<PrescricaoErrosEDiferencas>"
        "<DataPrescricao>2026-04-01</DataPrescricao>"
        "%s"
        "</PrescricaoErrosEDiferencas>" % content
    )


def _prestacao(
    prescription,
    billed="41.00",
    allowed="5.00",
    days_billed="31",
    days_paid="0",
    errors="",
    lines="",
    prescription_data="",
):
    return (
        "<PrestacoesErrosEDiferencas>"
        "<NumeroPrescricao>%s</NumeroPrescricao>"
        "<NumeroUtente>000000000</NumeroUtente>"
        "<QuantidadeLida>%s</QuantidadeLida>"
        "<QuantidadeCalculado>%s</QuantidadeCalculado>"
        "<ValorTotalLido>%s</ValorTotalLido>"
        "<ValorTotalCalculado>%s</ValorTotalCalculado>"
        "%s%s%s"
        "</PrestacoesErrosEDiferencas>"
        % (
            prescription,
            days_billed,
            days_paid,
            billed,
            allowed,
            errors,
            lines,
            prescription_data,
        )
    )


def _document(
    estado="Conferida Com Erros",
    total_billed="41.00",
    total_allowed="5.00",
    total_billed_taxed="41.00",
    total_allowed_taxed="5.00",
    oficio="Documento conferido. Com rectificações.",
    invoice_errors="",
    lote_errors="",
    claims="",
):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ApplicationResponse xmlns="urn:oasis:names:specification:ubl:'
        'schema:xsd:ApplicationResponse-2">'
        "<ID>999999999</ID>"
        "<DocumentResponse><Response>"
        "<ReferenceID>FT TEST/00001</ReferenceID>"
        "<Description>%s</Description>"
        "</Response></DocumentResponse>"
        "<UBLExtensions><UBLExtension><ExtensionContent>"
        "<ErrosEDiferencasCRDExtension>"
        "<FacturasErrosEDiferencas>"
        "<EstadoFactura>%s</EstadoFactura>"
        "<TotalFaturaLido>%s</TotalFaturaLido>"
        "<TotalFaturaCalculado>%s</TotalFaturaCalculado>"
        "<TotalFaturaIVALido>%s</TotalFaturaIVALido>"
        "<TotalFaturaIVACalculado>%s</TotalFaturaIVACalculado>"
        "%s"
        "<LoteErrosEDiferencas>"
        "<Numero>1</Numero>"
        "<TipoLote>992</TipoLote>"
        "%s%s"
        "</LoteErrosEDiferencas>"
        "</FacturasErrosEDiferencas>"
        "</ErrosEDiferencasCRDExtension>"
        "</ExtensionContent></UBLExtension></UBLExtensions>"
        "</ApplicationResponse>"
        % (
            oficio,
            estado,
            total_billed,
            total_allowed,
            total_billed_taxed,
            total_allowed_taxed,
            invoice_errors,
            lote_errors,
            claims,
        )
    )


class TestSpmsCheckProcess(SavepointComponentCase):
    """SavepointComponentCase builds the components registry itself:
    the global one only exists after a full server load, so a plain
    SavepointCase cannot resolve components at install time."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        # the SPMS flow relies on global tax rounding (production setting)
        cls.company.tax_calculation_rounding_method = "round_globally"
        cls.backend = cls.env.ref("l10n_pt_invoice_spms.spms_backend")
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
            "name": "SPMS Process Test Sales",
            "code": "TSPP",
            "type": "sale",
            "company_id": cls.company.id,
            "default_account_id": cls.income_account.id,
        }
        if "edi_format_ids" in cls.env["account.journal"]._fields:
            journal_vals["edi_format_ids"] = [(5, 0, 0)]
        cls.journal = cls.env["account.journal"].create(journal_vals)
        cls.partner = cls.env["res.partner"].create({"name": "Process Partner"})
        cls.invoice = cls._create_invoice(
            "FT TEST/00001", [("TESTP001", 31, 1.0), ("TESTP002", 10, 1.0)]
        )

    @classmethod
    def _create_invoice(cls, name, lines):
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
                            "name": "SPMS service",
                            "quantity": days,
                            "price_unit": price_unit,
                            "account_id": cls.income_account.id,
                            "tax_ids": [(5, 0, 0)],
                            "spms_prescription": prescription,
                        },
                    )
                    for prescription, days, price_unit in lines
                ],
            }
        )
        move.action_post()
        return move

    def _process(self, document, invoice=None):
        child = self.backend.create_record(
            "l10n_pt_spms_check",
            {
                "edi_exchange_state": "input_received",
                "model": "account.move",
                "res_id": (invoice or self.invoice).id,
            },
        )
        child._set_file_content(document)
        self.backend.exchange_process(child)
        return child

    def _result(self, invoice=None):
        return self.env["spms.invoice.check"].search(
            [("move_id", "=", (invoice or self.invoice).id)]
        )

    def _attachments(self, result):
        return self.env["ir.attachment"].search(
            [("res_model", "=", "spms.invoice.check"), ("res_id", "=", result.id)]
        )

    def test_full_document_maps_everything(self):
        document = _document(
            invoice_errors=_erro("D306"),
            lote_errors=_erro("A004"),
            claims=_prestacao(
                "TESTP001",
                errors=_erro("C011"),
                lines=_linha("REF1", _erro("C012")),
                prescription_data=_prescricao(_erro("C010")),
            ),
        )
        child = self._process(document)
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self._result()
        self.assertEqual(len(result), 1)
        self.assertEqual(result.check_state, "with_errors")
        self.assertAlmostEqual(result.total_billed, 41.0)
        self.assertAlmostEqual(result.total_allowed, 5.0)
        self.assertAlmostEqual(result.total_billed_taxed, 41.0)
        self.assertAlmostEqual(result.total_allowed_taxed, 5.0)
        self.assertAlmostEqual(result.credit_official, 36.0)
        self.assertIn("Documento conferido", result.oficio)
        self.assertTrue(result.fetch_date)
        self.assertFalse(result.completeness_warning)
        self.assertEqual(result.error_count, 5)
        by_level = {row.level: row for row in result.error_ids}
        self.assertEqual(
            set(by_level), {"invoice", "lote", "prestacao", "linha", "prescricao"}
        )
        self.assertFalse(by_level["invoice"].prescription)
        self.assertEqual(by_level["lote"].lot_type, "992")
        self.assertEqual(by_level["lote"].lot_number, "1")
        for level in ("prestacao", "linha", "prescricao"):
            row = by_level[level]
            self.assertEqual(row.prescription, "TESTP001")
            self.assertAlmostEqual(row.amount_billed, 41.0)
            self.assertAlmostEqual(row.amount_allowed, 5.0)
            self.assertAlmostEqual(row.amount_difference, 36.0)
            self.assertAlmostEqual(row.days_billed, 31.0)
            self.assertEqual(
                row.move_line_id.spms_prescription,
                "TESTP001",
            )
        self.assertEqual(by_level["linha"].provider_system_ref, "REF1")
        attachment = self._attachments(result)
        self.assertEqual(len(attachment), 1)
        self.assertEqual(base64.b64decode(attachment.datas).decode(), document)
        self.assertFalse(result.generation_error)
        self.assertEqual(result.state, "done")
        draft = result.credit_note_move_id
        self.assertEqual(draft.state, "draft")
        self.assertEqual(draft.move_type, "out_refund")
        self.assertAlmostEqual(draft.amount_total, 36.0)

    def test_without_errors_closes_as_zero_official(self):
        document = _document(
            estado="Conferida Sem Erros",
            total_billed="10.00",
            total_allowed="10.00",
            total_billed_taxed="10.60",
            total_allowed_taxed="10.60",
        )
        child = self._process(document)
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self._result()
        self.assertEqual(result.check_state, "without_errors")
        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.state, "zero_official")
        self.assertFalse(
            self.env["account.move"].search(
                [
                    ("move_type", "=", "out_refund"),
                    ("journal_id", "=", self.journal.id),
                ]
            )
        )

    def test_without_errors_with_credit_holds_in_error(self):
        # a no-errors verdict whose totals still leave credit is not a
        # recognised outcome: held for human review, never parked in Ready
        document = _document(
            estado="Conferida Sem Erros",
            total_billed="41.00",
            total_allowed="5.00",
            total_billed_taxed="41.00",
            total_allowed_taxed="5.00",
        )
        child = self._process(document)
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self._result()
        self.assertEqual(result.check_state, "without_errors")
        self.assertEqual(result.state, "error")
        self.assertIn("recognised outcome", result.error_message)
        self.assertFalse(result.credit_note_move_id)
        self.assertFalse(result.generation_error)

    def test_result_without_verdict_holds_in_error(self):
        # a result with no verdict and no incident must never close
        # silently (out-of-band creations: imports, future code)
        result = self.env["spms.invoice.check"].create({"move_id": self.invoice.id})
        self.assertEqual(result.state, "error")
        self.assertIn("recognised outcome", result.error_message)

    def test_unknown_code_autocreates_pending_type(self):
        document = _document(
            claims=_prestacao("TESTP001", errors=_erro("Z998", "Nova mensagem"))
        )
        self._process(document)
        row = self._result().error_ids
        self.assertEqual(row.code, "Z998")
        self.assertEqual(row.error_type_id.description, "Nova mensagem")

    def test_unmatched_prescription_has_no_link_and_no_parse_error(self):
        document = _document(claims=_prestacao("TESTMISSING", errors=_erro("C011")))
        child = self._process(document)
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self._result()
        row = result.error_ids
        self.assertEqual(row.prescription, "TESTMISSING")
        self.assertFalse(row.move_line_id)
        # parsing never blocks; the generation reports the unmatched
        self.assertEqual(result.state, "error")
        self.assertIn("not matched", result.generation_error)
        self.assertEqual(result.error_message, result.generation_error)
        self.assertFalse(result.credit_note_move_id)

    def test_ambiguous_prescription_stays_unlinked(self):
        invoice = self._create_invoice(
            "FT TEST/00002", [("TESTPDUP", 10, 1.0), ("TESTPDUP", 5, 2.0)]
        )
        document = _document(claims=_prestacao("TESTPDUP", errors=_erro("C011")))
        self._process(document, invoice=invoice)
        row = self._result(invoice).error_ids
        self.assertFalse(row.move_line_id)

    def test_multi_claim_counts_per_level(self):
        document = _document(
            claims=(
                _prestacao(
                    "TESTP001",
                    errors=_erro("C011") + _erro("D306"),
                    lines=_linha("REF1", _erro("C012") + _erro("C012")),
                )
                + _prestacao("TESTP002", errors=_erro("C011"))
            )
        )
        self._process(document)
        result = self._result()
        self.assertEqual(result.error_count, 5)
        self.assertEqual(
            len(result.error_ids.filtered(lambda r: r.level == "prestacao")), 3
        )
        self.assertEqual(
            len(result.error_ids.filtered(lambda r: r.level == "linha")), 2
        )
        codes = set(result.error_codes.split(" / "))
        self.assertEqual(codes, {"C011", "D306", "C012"})

    def test_completeness_warning_fires_on_unknown_position(self):
        document = _document(
            claims=_prestacao(
                "TESTP001",
                errors=_erro("C011"),
                prescription_data=_prescricao(
                    "<LinhaPrescricaoErrosEDiferencas>%s"
                    "</LinhaPrescricaoErrosEDiferencas>" % _erro("C999")
                ),
            )
        )
        child = self._process(document)
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self._result()
        self.assertEqual(result.error_count, 1)
        self.assertTrue(result.completeness_warning)
        self.assertIn("1", result.completeness_warning)

    def test_empty_code_counts_in_the_completeness_warning(self):
        document = _document(claims=_prestacao("TESTP001", errors=_erro("")))
        self._process(document)
        result = self._result()
        self.assertEqual(result.error_count, 0)
        self.assertTrue(result.completeness_warning)

    def test_reprocess_is_idempotent(self):
        # held generation: the result never locks, reprocess stays allowed
        document = _document(claims=_prestacao("TESTMISSING", errors=_erro("C011")))
        child = self._process(document)
        result = self._result()
        self.assertEqual(result.error_count, 1)
        child.edi_exchange_state = "input_received"
        self.backend.exchange_process(child)
        result = self._result()
        self.assertEqual(len(result), 1)
        self.assertEqual(result.error_count, 1)
        self.assertEqual(len(self._attachments(result)), 1)

    def test_definitive_result_supersedes_incident(self):
        self.env["spms.invoice.check"].create(
            {"move_id": self.invoice.id, "ws_incident_code": "301"}
        )
        self.assertEqual(self._result().state, "error")
        self._process(_document(claims=_prestacao("TESTP001", errors=_erro("C011"))))
        result = self._result()
        self.assertFalse(result.ws_incident_code)
        self.assertFalse(result.error_message)
        self.assertEqual(result.state, "done")
        self.assertTrue(result.credit_note_move_id)

    def test_locked_result_blocks_reprocessing(self):
        refund = self.env["account.move"].create(
            {
                "move_type": "out_refund",
                "partner_id": self.partner.id,
                "journal_id": self.journal.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "refund",
                            "quantity": 1,
                            "price_unit": 5.0,
                            "account_id": self.income_account.id,
                            "tax_ids": [(5, 0, 0)],
                        },
                    )
                ],
            }
        )
        result = self.env["spms.invoice.check"].create(
            {
                "move_id": self.invoice.id,
                "check_state": "with_errors",
                "total_billed_taxed": 10.0,
            }
        )
        result.write({"credit_note_move_id": refund.id, "state": "done"})
        self.assertTrue(result.official_locked)
        child = self._process(_document())
        self.assertEqual(child.edi_exchange_state, "input_processed_error")
        self.assertIn("credit note", child.exchange_error)
        self.assertAlmostEqual(result.total_billed_taxed, 10.0)

    def test_garbage_document_marks_processing_error(self):
        child = self._process("this is not xml at all")
        self.assertEqual(child.edi_exchange_state, "input_processed_error")
        self.assertFalse(self._result())

    def test_missing_extension_marks_processing_error(self):
        child = self._process("<ApplicationResponse><ID>x</ID></ApplicationResponse>")
        self.assertEqual(child.edi_exchange_state, "input_processed_error")
        self.assertIn("FacturasErrosEDiferencas", child.exchange_error)
        self.assertFalse(self._result())

    def test_unknown_estado_marks_processing_error(self):
        child = self._process(_document(estado="Estado Misterioso"))
        self.assertEqual(child.edi_exchange_state, "input_processed_error")
        self.assertFalse(self._result())

    def test_unparseable_total_marks_processing_error(self):
        # the official totals are the money: an unreadable one rejects
        # the document instead of silently becoming 0.0
        child = self._process(_document(total_billed_taxed="1.234,56"))
        self.assertEqual(child.edi_exchange_state, "input_processed_error")
        self.assertIn("TotalFaturaIVALido", child.exchange_error)
        self.assertIn("1.234,56", child.exchange_error)
        self.assertFalse(self._result())

    def test_unparseable_claim_amount_warns_and_continues(self):
        # claim amounts only shape the breakdown: stored as 0 with a
        # completeness warning, and comma decimals keep working
        document = _document(
            claims=_prestacao("TESTP001", billed="12 345,67", errors=_erro("C011"))
            + _prestacao("TESTP002", billed="10,00", errors=_erro("C012")),
        )
        child = self._process(document)
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self._result()
        self.assertIn("ValorTotalLido", result.completeness_warning)
        self.assertIn("TESTP001", result.completeness_warning)
        by_prescription = {row.prescription: row for row in result.error_ids}
        self.assertAlmostEqual(by_prescription["TESTP001"].amount_billed, 0.0)
        self.assertAlmostEqual(by_prescription["TESTP002"].amount_billed, 10.0)

    def test_trigger_holds_on_missing_adjustment_product(self):
        self.company.spms_adjustment_product_id = False
        document = _document(
            total_billed_taxed="43.46",
            total_allowed_taxed="5.30",
            claims=_prestacao("TESTP001", errors=_erro("C011")),
        )
        child = self._process(document)
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self._result()
        self.assertEqual(result.state, "error")
        self.assertIn("adjustment line product", result.generation_error)
        self.assertEqual(result.error_message, result.generation_error)
        self.assertFalse(result.credit_note_move_id)

    def test_trigger_retry_after_configuring_adjustment(self):
        self.company.spms_adjustment_product_id = False
        document = _document(
            total_billed_taxed="43.46",
            total_allowed_taxed="5.30",
            claims=_prestacao("TESTP001", errors=_erro("C011")),
        )
        child = self._process(document)
        self.assertEqual(self._result().state, "error")
        product = self.env["product.product"].create(
            {
                "name": "SPMS adjustment test",
                "type": "service",
                "taxes_id": [(6, 0, self.tax6.ids)],
            }
        )
        self.company.spms_adjustment_product_id = product
        child.edi_exchange_state = "input_received"
        self.backend.exchange_process(child)
        result = self._result()
        self.assertEqual(result.state, "done")
        self.assertFalse(result.generation_error)
        self.assertFalse(result.error_message)
        self.assertAlmostEqual(result.credit_note_move_id.amount_total, 38.16)

    def test_trigger_preexisting_foreign_note_blocks_generation(self):
        invoice = self._create_invoice("FT TEST/00004", [("TESTP201", 31, 1.0)])
        wizard = self.env["account.move.reversal"].create(
            {
                "move_ids": [(6, 0, invoice.ids)],
                "refund_method": "refund",
                "date_mode": "custom",
                "date": fields.Date.context_today(invoice),
                "company_id": self.company.id,
            }
        )
        wizard.reverse_moves()
        foreign = invoice.reversal_move_id
        child = self._process(
            _document(claims=_prestacao("TESTP201", errors=_erro("C011"))),
            invoice=invoice,
        )
        self.assertEqual(child.edi_exchange_state, "input_processed")
        result = self._result(invoice)
        self.assertEqual(result.state, "error")
        self.assertFalse(result.generation_error)
        self.assertIn(foreign.display_name, result.error_message)
        self.assertFalse(result.credit_note_move_id)

    def test_trigger_failure_does_not_drag_other_results(self):
        bad_child = self._process(
            _document(claims=_prestacao("TESTMISSING", errors=_erro("C011")))
        )
        good_invoice = self._create_invoice("FT TEST/00005", [("TESTP301", 31, 1.0)])
        good_child = self._process(
            _document(
                total_billed="31.00",
                total_allowed="0.00",
                total_billed_taxed="31.00",
                total_allowed_taxed="0.00",
                # a real diff-claim always carries an error (golden evidence)
                claims=_prestacao(
                    "TESTP301", billed="31.00", allowed="0.00", errors=_erro("C011")
                ),
            ),
            invoice=good_invoice,
        )
        self.assertEqual(bad_child.edi_exchange_state, "input_processed")
        self.assertEqual(good_child.edi_exchange_state, "input_processed")
        self.assertEqual(self._result().state, "error")
        good_check = self._result(good_invoice)
        self.assertEqual(good_check.state, "done")
        self.assertAlmostEqual(good_check.credit_note_move_id.amount_total, 31.0)
