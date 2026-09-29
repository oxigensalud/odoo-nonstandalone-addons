# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.l10n_pt_invoice_spms_verification.tests.common import (
    SpmsVerificationCase,
)


class TestSpmsVerificationFiscalDocument(SpmsVerificationCase):
    """The notes the verification generates are fiscal documents of the
    certified invoicing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the localization certifies the documents of this company, and
        # posts them only with a VAT of its own groups
        cls.company.pt_invoicing = True
        cls.tax6.tax_group_id = cls.env.ref("ptplus.tax_group_iva_6")

    def test_generate_from_another_company_creates_fiscal_credit_note(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        # the jobs that generate the notes run as a user of another company
        other_company = self.env["res.company"].create({"name": "Other company"})
        self.env.user.company_id = other_company
        draft = result._generate_note()
        self.assertEqual(draft.fiscal_document_type_id.type, "NC")

    def test_generate_from_another_company_creates_fiscal_debit_note(self):
        move, rows = self._debit_invoice()
        result = self._create_result(move, rows, credit_official=-2.12)
        # the jobs that generate the notes run as a user of another company
        other_company = self.env["res.company"].create({"name": "Other company"})
        self.env.user.company_id = other_company
        draft = result._generate_note()
        self.assertEqual(draft.fiscal_document_type_id.type, "ND")
