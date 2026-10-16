# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import SpmsVerificationCase


class TestSpmsVerificationFiscalDocument(SpmsVerificationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ptplus on, and posting needs its document types and a tax of one of
        # its VAT groups
        cls.company.pt_invoicing = True
        cls.company.l10n_pt_create_default_fiscal_doc_types(["invoice"])
        cls.tax6.tax_group_id = cls.env.ref("ptplus.tax_group_iva_6")

    def test_generate_from_another_company_creates_fiscal_credit_note(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        # jobs run as a user of another company
        other_company = self.env["res.company"].create({"name": "Other company"})
        self.env.user.company_id = other_company
        draft = result._generate_note()
        self.assertEqual(draft.fiscal_document_type_id.type, "NC")

    def test_generate_from_another_company_creates_fiscal_debit_note(self):
        move, rows = self._debit_invoice()
        result = self._create_result(move, rows, credit_official=-2.12)
        # jobs run as a user of another company
        other_company = self.env["res.company"].create({"name": "Other company"})
        self.env.user.company_id = other_company
        draft = result._generate_note()
        self.assertEqual(draft.fiscal_document_type_id.type, "ND")
