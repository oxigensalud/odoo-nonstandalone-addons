# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from datetime import date, datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from odoo.addons.component.tests.common import ComponentMixin

from .common import SpmsInvoiceCase

CERTIFICATE_PASSWORD = "spms-test"
NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "crd": "urn:acss:ccf:facturacaoelectronica:schema:xsd:CRD",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}


def _test_certificate():
    """A self-signed certificate in PKCS#12, as the company stores it."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SPMS test")])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"spms",
        key,
        certificate,
        None,
        serialization.BestAvailableEncryption(CERTIFICATE_PASSWORD.encode()),
    )


class TestSpmsGenerate(SpmsInvoiceCase, ComponentMixin):
    """The SPMS document is built by the module's UBL builder on top of the
    core one and signed: the shape production sends to the CCF."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setUpComponent()
        cls.company.vat = "PT999999990"
        cls.company.spms_certificate = base64.b64encode(_test_certificate())
        cls.company.spms_certificate_password = CERTIFICATE_PASSWORD
        cls.partner.spms_assigned_id = "12345678"
        cls.product.spms_lot_id = cls.env["spms.lot"].create(
            {"name": "SPMS test lot", "code": "TT"}
        )

    def setUp(self):
        super().setUp()
        self.setUpComponentRegistryReady()

    def _spms_invoice(self):
        invoice = self._create_invoice()
        invoice.invoice_line_ids.write(
            {
                "spms_prescription": "1051000007032191606",
                "spms_start_date": date(2026, 4, 1),
                "spms_end_date": date(2026, 4, 30),
            }
        )
        invoice.action_post()
        return invoice

    def _generate(self, move):
        record = self.backend.create_record(
            "l10n_pt_spms", {"model": "account.move", "res_id": move.id}
        )
        self.backend.exchange_generate(record)
        self.assertEqual(
            record.edi_exchange_state, "output_pending", record.exchange_error
        )
        return etree.fromstring(record._get_file_content(as_bytes=True))

    def test_invoice_document(self):
        invoice = self._spms_invoice()
        root = self._generate(invoice)
        self.assertEqual(etree.QName(root).localname, "Invoice")
        self.assertEqual(
            root.findtext("cbc:ID", namespaces=NS), invoice._get_spms_invoice_number()
        )
        self.assertEqual(root.findtext("cbc:InvoiceTypeCode", namespaces=NS), "FF")
        self.assertEqual(
            root.findtext("cbc:UBLVersionID", namespaces=NS),
            "UBL 2.0 CS (2006.10) + SIC (2007.03)",
        )
        self.assertEqual(
            root.findtext(".//cbc:CustomerAssignedAccountID", namespaces=NS),
            "12345678",
        )
        self.assertEqual(
            root.findtext("cac:InvoicePeriod/cbc:StartDate", namespaces=NS),
            "2026-04-01",
        )
        self.assertEqual(
            root.findtext("cac:InvoicePeriod/cbc:EndDate", namespaces=NS),
            "2026-04-30",
        )
        self.assertEqual(
            {node.text for node in root.findall(".//cac:TaxScheme/cbc:ID", NS)},
            {"PT IVA"},
        )
        # one line per lot, carrying the number of dispensations
        lines = root.findall("cac:InvoiceLine", NS)
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            lines[0].findtext(
                "cac:Item/cac:StandardItemIdentification/cbc:ID", namespaces=NS
            ),
            "TT",
        )
        self.assertEqual(
            lines[0].findtext(
                "cac:Item/cac:AdditionalItemProperty/cbc:Value", namespaces=NS
            ),
            "1",
        )
        # the CRD extension with the dispensations, and the signature
        self.assertEqual(root.findtext(".//crd:NumeroLotes", namespaces=NS), "1")
        self.assertEqual(
            [node.text for node in root.findall(".//crd:NumeroPrescricao", NS)],
            ["1051000007032191606"],
        )
        self.assertIsNotNone(root.find(".//ds:Signature", NS))

    def test_credit_note_document(self):
        invoice = self._spms_invoice()
        note = invoice._reverse_moves([{"invoice_date": date(2026, 6, 15)}])
        note.action_post()
        root = self._generate(note)
        self.assertEqual(etree.QName(root).localname, "CreditNote")
        self.assertEqual(
            root.findtext(
                "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID",
                namespaces=NS,
            ),
            invoice._get_spms_invoice_number(),
        )
        self.assertIsNone(root.find(".//crd:CRDExtension", NS))
        self.assertEqual(
            {node.text for node in root.findall(".//cac:TaxScheme/cbc:ID", NS)},
            {"PT IVA"},
        )
        self.assertIsNotNone(root.find(".//ds:Signature", NS))
