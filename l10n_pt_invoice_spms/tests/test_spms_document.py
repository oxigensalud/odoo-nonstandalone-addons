# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo.exceptions import UserError

from .common import SpmsInvoiceCase

CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
CRD = "{urn:acss:ccf:facturacaoelectronica:schema:xsd:CRD}"


class TestSpmsDocument(SpmsInvoiceCase):
    """The UBL document the sender builds for each kind of move."""

    def test_invoice_document_is_an_invoice_with_the_crd_extension(self):
        invoice = self._create_invoice()
        invoice.action_post()
        document = self._document(invoice)
        self.assertEqual(
            document.tag,
            "{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice",
        )
        self.assertEqual(len(document.findall(".//" + CRD + "CRDExtension")), 1)
        self.assertIsNone(document.find(CAC + "BillingReference"))

    def test_credit_note_document_is_a_credit_note_of_the_invoice(self):
        invoice = self._create_invoice(invoice_date=date(2026, 5, 31))
        invoice.action_post()
        credit_note = self._create_credit_note(invoice, date(2026, 6, 15))
        credit_note.action_post()
        document = self._document(credit_note)
        self.assertEqual(
            document.tag,
            "{urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2}CreditNote",
        )
        self.assertIsNone(document.find(".//" + CRD + "CRDExtension"))
        reference = document.find(
            CAC + "BillingReference/" + CAC + "InvoiceDocumentReference"
        )
        self.assertEqual(reference.findtext(CBC + "ID"), "TSPMS202605-1")
        self.assertEqual(reference.findtext(CBC + "IssueDate"), "2026-05-31")

    def test_credit_note_document_names_the_invoice_by_its_invoice_date(self):
        invoice = self._create_invoice(
            invoice_date=date(2026, 5, 30), accounting_date=date(2026, 5, 31)
        )
        invoice.action_post()
        credit_note = self._create_credit_note(invoice, date(2026, 6, 15))
        credit_note.action_post()
        document = self._document(credit_note)
        reference = document.find(
            CAC + "BillingReference/" + CAC + "InvoiceDocumentReference"
        )
        self.assertEqual(reference.findtext(CBC + "IssueDate"), "2026-05-30")

    def test_debit_note_document_is_a_debit_note_of_the_invoice(self):
        invoice = self._create_invoice(invoice_date=date(2026, 5, 31))
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        document = self._document(debit_note)
        self.assertEqual(
            document.tag,
            "{urn:oasis:names:specification:ubl:schema:xsd:DebitNote-2}DebitNote",
        )
        self.assertIsNone(document.find(".//" + CRD + "CRDExtension"))
        reference = document.find(
            CAC + "BillingReference/" + CAC + "InvoiceDocumentReference"
        )
        self.assertEqual(reference.findtext(CBC + "ID"), "TSPMS202605-1")
        self.assertEqual(reference.findtext(CBC + "IssueDate"), "2026-05-31")

    def test_debit_note_document_note_names_the_rectified_invoice(self):
        invoice = self._create_invoice(invoice_date=date(2026, 5, 31))
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        document = self._document(debit_note)
        self.assertEqual(
            document.findtext(CBC + "Note"),
            "Nota de Débito TSPMS202606-1 para rectificação à fatura "
            "TSPMS202605-1 de 2026-05-31",
        )

    def test_debit_note_document_names_the_invoice_by_its_invoice_date(self):
        invoice = self._create_invoice(
            invoice_date=date(2026, 5, 30), accounting_date=date(2026, 5, 31)
        )
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        document = self._document(debit_note)
        reference = document.find(
            CAC + "BillingReference/" + CAC + "InvoiceDocumentReference"
        )
        self.assertEqual(reference.findtext(CBC + "IssueDate"), "2026-05-30")
        self.assertEqual(
            document.findtext(CBC + "Note"),
            "Nota de Débito TSPMS202606-1 para rectificação à fatura "
            "TSPMS202605-1 de 2026-05-30",
        )

    def test_debit_note_document_requests_the_total_of_the_note(self):
        invoice = self._create_invoice(price_unit=100.0, tax_rate=23)
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        document = self._document(debit_note)
        total = document.find(CAC + "RequestedMonetaryTotal")
        self.assertEqual(total.findtext(CBC + "TaxExclusiveAmount"), "100.00")
        self.assertEqual(total.findtext(CBC + "PayableAmount"), "123.00")
        self.assertIsNone(document.find(CAC + "LegalMonetaryTotal"))

    def test_debit_note_document_line_debits_the_amount_once(self):
        invoice = self._create_invoice(price_unit=100.0, tax_rate=23)
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        document = self._document(debit_note)
        lines = document.findall(CAC + "DebitNoteLine")
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line.findtext(CBC + "ID"), "1")
        self.assertEqual(line.findtext(CBC + "DebitedQuantity"), "1")
        self.assertEqual(line.findtext(CBC + "LineExtensionAmount"), "100.00")
        self.assertEqual(
            line.find(CAC + "TaxTotal").findtext(CBC + "TaxAmount"), "23.00"
        )
        self.assertIsNone(line.find(CAC + "Item"))
        self.assertIsNone(line.find(CAC + "Price"))

    def test_document_with_two_origins_is_refused(self):
        invoice = self._create_invoice()
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        debit_note.reversed_entry_id = invoice
        with self.assertRaisesRegex(UserError, "credit or a debit note"):
            self._document(debit_note)

    def test_document_of_a_debit_note_of_a_credit_note_is_refused(self):
        invoice = self._create_invoice()
        invoice.action_post()
        credit_note = self._create_credit_note(invoice, date(2026, 6, 15))
        credit_note.action_post()
        debit_note = self._create_debit_note(credit_note, date(2026, 6, 20))
        with self.assertRaisesRegex(UserError, "itself a note"):
            self._document(debit_note)

    def test_document_of_a_credit_note_of_a_debit_note_is_refused(self):
        invoice = self._create_invoice()
        invoice.action_post()
        debit_note = self._create_debit_note(invoice, date(2026, 6, 15))
        debit_note.action_post()
        credit_note = self._create_credit_note(debit_note, date(2026, 6, 20))
        credit_note.action_post()
        with self.assertRaisesRegex(UserError, "itself a note"):
            self._document(credit_note)
