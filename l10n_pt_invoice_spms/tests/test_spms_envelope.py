# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from .common import SpmsInvoiceCase

BODY = "{http://schemas.xmlsoap.org/soap/envelope/}Body/"
FAC = "{http://facturaElectronica.service.cc.ccf/}"


class TestSpmsEnvelope(SpmsInvoiceCase):
    """The CCF operation each kind of move is submitted with."""

    def test_invoice_is_submitted_as_a_factura(self):
        invoice = self._create_invoice(invoice_date=date(2026, 5, 31))
        invoice.action_post()
        envelope = self._envelope(invoice)
        factura = envelope.find(BODY + FAC + "submeterFacturaElectronicaCRD/factura")
        self.assertIsNotNone(factura)
        self.assertEqual(factura.findtext("numeroFactura"), "TSPMS202605-1")
        self.assertEqual(factura.findtext("dataFactura"), "2026-05-31")
        self.assertEqual(factura.findtext("ficheiroComprimido"), "N")
        self.assertIsNone(factura.find("tipoNota"))
        self.assertIsNone(factura.find("numeroNota"))

    def test_credit_note_is_submitted_as_a_nota_de_credito(self):
        invoice = self._create_invoice(invoice_date=date(2026, 5, 31))
        invoice.action_post()
        credit_note = self._create_credit_note(invoice, date(2026, 6, 15))
        credit_note.action_post()
        envelope = self._envelope(credit_note)
        nota = envelope.find(BODY + FAC + "submeterNotaCredDebCRD/nota")
        self.assertIsNotNone(nota)
        self.assertEqual(nota.findtext("tipoNota"), "C")
        self.assertEqual(nota.findtext("numeroNota"), "RTSPMS202606-1")
        self.assertEqual(nota.findtext("numeroFactura"), "TSPMS202605-1")
        self.assertEqual(nota.findtext("dataFactura"), "2026-05-31")
        self.assertIsNone(nota.find("ficheiroComprimido"))

    def test_debit_note_is_submitted_as_a_nota_de_debito(self):
        invoice = self._create_invoice(invoice_date=date(2026, 5, 31))
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        envelope = self._envelope(debit_note)
        nota = envelope.find(BODY + FAC + "submeterNotaCredDebCRD/nota")
        self.assertIsNotNone(nota)
        self.assertEqual(nota.findtext("tipoNota"), "D")
        self.assertEqual(nota.findtext("numeroNota"), "TSPMS202606-1")
        self.assertEqual(nota.findtext("numeroFactura"), "TSPMS202605-1")
        self.assertEqual(nota.findtext("dataFactura"), "2026-05-31")
        self.assertIsNone(nota.find("ficheiroComprimido"))

    def test_debit_note_is_submitted_under_the_invoice_date(self):
        invoice = self._create_invoice(
            invoice_date=date(2026, 5, 30), accounting_date=date(2026, 5, 31)
        )
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        nota = self._envelope(debit_note).find(
            BODY + FAC + "submeterNotaCredDebCRD/nota"
        )
        self.assertEqual(nota.findtext("dataFactura"), "2026-05-30")
