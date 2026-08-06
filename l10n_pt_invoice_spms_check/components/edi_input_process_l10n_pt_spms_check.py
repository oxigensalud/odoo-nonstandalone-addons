# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from lxml import etree

from odoo import _, fields
from odoo.exceptions import UserError

from odoo.addons.component.core import Component

_logger = logging.getLogger(__name__)


def _local(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def _children(element, name):
    """Direct children only: a claim's ValorTotalLido must never be
    confused with the one of its nested lines."""
    return [child for child in element if _local(child.tag) == name]


def _child_text(element, name):
    nodes = _children(element, name)
    return (nodes[0].text or "").strip() if nodes else ""


def _child_float(element, name):
    text = _child_text(element, name).replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _find_first(root, name):
    for element in root.iter():
        if _local(element.tag) == name:
            return element
    return None


class EdiInputProcessL10nPtSpmsCheck(Component):
    """Turn a fetched check document into the invoice's check result.

    Everything is located by local name: the WSDL does not publish the
    extension types. Patient fields (e.g. NumeroUtente) have no model
    column on purpose and are never stored.
    """

    _name = "edi.input.process.l10n_pt_spms_check"
    _usage = "input.process"
    _backend_type = "l10n_pt_spms"
    _exchange_type = "l10n_pt_spms_check"
    _inherit = "edi.component.input.mixin"

    def process(self):
        exchange_record = self.exchange_record
        move = exchange_record.record
        if not move:
            raise UserError(
                _("The exchange record %s is not linked to an invoice.")
                % exchange_record.identifier
            )
        raw = exchange_record._get_file_content(as_bytes=True)
        # resolve_entities off: XXE hardening on external data
        parser = etree.XMLParser(resolve_entities=False)
        try:
            root = etree.fromstring(raw, parser)
        except etree.XMLSyntaxError as err:
            raise UserError(
                _(
                    "The check document of %(invoice)s is not parseable XML: "
                    "%(error)s"
                )
                % {"invoice": move.display_name, "error": err}
            ) from err
        fact = _find_first(root, "FacturasErrosEDiferencas")
        if fact is None:
            raise UserError(
                _(
                    "The check document of %s carries no "
                    "FacturasErrosEDiferencas extension."
                )
                % move.display_name
            )
        check_state = self._parse_check_state(move, fact)
        rows = self._parse_error_rows(move, fact)
        check_vals = {
            "check_state": check_state,
            "fetch_date": fields.Datetime.now(),
            "total_billed": _child_float(fact, "TotalFaturaLido"),
            "total_allowed": _child_float(fact, "TotalFaturaCalculado"),
            "total_billed_taxed": _child_float(fact, "TotalFaturaIVALido"),
            "total_allowed_taxed": _child_float(fact, "TotalFaturaIVACalculado"),
            "oficio": self._parse_oficio(root),
            "completeness_warning": self._completeness_warning(root, len(rows)),
            "ws_incident_code": False,
            "generation_error": False,
        }
        check = self._store_check(move, check_vals, rows)
        self._attach_document(move, check, exchange_record)
        # a definitive with-errors result goes straight to its draft
        # credit note; a failure holds this result only, with reason
        check._generate_credit_note_or_hold()
        return _(
            "SPMS check result of %(invoice)s processed: %(state)s, "
            "%(count)s error rows."
        ) % {
            "invoice": move.display_name,
            "state": check_state,
            "count": len(rows),
        }

    def _parse_check_state(self, move, fact):
        estado = _child_text(fact, "EstadoFactura").lower()
        if "sem erros" in estado:
            return "without_errors"
        if "com erros" in estado:
            return "with_errors"
        raise UserError(
            _(
                "The check document of %(invoice)s reports an unknown "
                "EstadoFactura: %(estado)s"
            )
            % {"invoice": move.display_name, "estado": estado or _("(empty)")}
        )

    def _parse_error_rows(self, move, fact):
        """Error rows at their five anchors, in document order."""
        error_type_model = self.env["spms.error.type"]
        line_by_prescription = {}
        for line in move.invoice_line_ids:
            if line.spms_prescription:
                line_by_prescription.setdefault(line.spms_prescription, [])
                line_by_prescription[line.spms_prescription].append(line.id)
        rows = []

        def add_rows(anchor_element, level, anchor_vals):
            for erro in _children(anchor_element, "Erro"):
                error_type = error_type_model._get_or_create(
                    _child_text(erro, "Codigo"), _child_text(erro, "Mensagem")
                )
                if not error_type:
                    # unnamed error: left to the completeness warning
                    continue
                rows.append(
                    dict(
                        anchor_vals,
                        level=level,
                        error_type_id=error_type.id,
                        description=_child_text(erro, "Mensagem"),
                    )
                )

        add_rows(fact, "invoice", {})
        for lote in _children(fact, "LoteErrosEDiferencas"):
            lote_vals = {
                "lot_type": _child_text(lote, "TipoLote"),
                "lot_number": _child_text(lote, "Numero"),
            }
            add_rows(lote, "lote", lote_vals)
            for claim in _children(lote, "PrestacoesErrosEDiferencas"):
                prescription = _child_text(claim, "NumeroPrescricao")
                line_ids = line_by_prescription.get(prescription, [])
                claim_vals = {
                    "prescription": prescription,
                    "amount_billed": _child_float(claim, "ValorTotalLido"),
                    "amount_allowed": _child_float(claim, "ValorTotalCalculado"),
                    "days_billed": _child_float(claim, "QuantidadeLida"),
                    "days_paid": _child_float(claim, "QuantidadeCalculado"),
                    "move_line_id": line_ids[0] if len(line_ids) == 1 else False,
                }
                add_rows(claim, "prestacao", claim_vals)
                for line in _children(claim, "LinhaPrestacaoErrosEDiferencas"):
                    add_rows(
                        line,
                        "linha",
                        dict(
                            claim_vals,
                            provider_system_ref=_child_text(line, "SistemaPrescrito"),
                        ),
                    )
                for prescription_data in _children(claim, "PrescricaoErrosEDiferencas"):
                    add_rows(prescription_data, "prescricao", claim_vals)
        return rows

    def _completeness_warning(self, root, row_count):
        """Any Erro the anchored walk did not turn into a row is
        reported on the result, never blocking."""
        total = sum(1 for element in root.iter() if _local(element.tag) == "Erro")
        missing = total - row_count
        if missing <= 0:
            return False
        return _(
            "%(count)s of the %(total)s errors of the check document sit at "
            "positions this parser does not know: the error list below is "
            "incomplete. The raw document attached to this result is the "
            "authority."
        ) % {"count": missing, "total": total}

    def _parse_oficio(self, root):
        response = _find_first(root, "Response")
        return _child_text(response, "Description") if response is not None else False

    def _store_check(self, move, check_vals, rows):
        """Upsert the 1:1 result and rebuild its rows (idempotent)."""
        check = move.spms_invoice_check_ids[:1]
        if check and check.official_locked:
            raise UserError(
                _(
                    "The check result of %s is already carried by a live "
                    "credit note; cancel that credit note before "
                    "reprocessing the document."
                )
                % move.display_name
            )
        if check:
            check.error_ids.unlink()
            check.write(check_vals)
        else:
            check = self.env["spms.invoice.check"].create(
                dict(check_vals, move_id=move.id)
            )
        if rows:
            self.env["spms.invoice.check.error"].create(
                [dict(row, result_id=check.id) for row in rows]
            )
        return check

    def _attach_document(self, move, check, exchange_record):
        """One attachment per result: reprocessing replaces its content."""
        name = "%s-spms-check.xml" % (move.name or "invoice").replace("/", "_")
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", check._name),
                ("res_id", "=", check.id),
                ("name", "=", name),
            ],
            limit=1,
        )
        values = {
            "datas": exchange_record.exchange_file,
            "mimetype": "application/xml",
        }
        if attachment:
            attachment.write(values)
        else:
            self.env["ir.attachment"].create(
                dict(
                    values,
                    name=name,
                    res_model=check._name,
                    res_id=check.id,
                )
            )
