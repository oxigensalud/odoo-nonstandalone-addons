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
    """0.0 for an absent or empty child; ValueError when its text is
    present but not a number (comma decimals accepted)."""
    text = _child_text(element, name)
    if not text:
        return 0.0
    return float(text.replace(",", "."))


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

    _name = "edi.input.process.l10n_pt_spms.l10n_pt_spms_check"
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
        problems = []
        lines, errors = self._parse_lines(move, fact, problems)
        error_count = len(errors) + sum(len(line_errors) for _, line_errors in lines)
        result_vals = {
            "name": _child_text(root, "ID") or False,
            "document_date": self._parse_document_date(root, problems),
            "check_state": check_state,
            "fetch_date": fields.Datetime.now(),
            "total_billed": self._parse_total(move, fact, "TotalFaturaLido"),
            "total_allowed": self._parse_total(move, fact, "TotalFaturaCalculado"),
            "total_billed_taxed": self._parse_total(move, fact, "TotalFaturaIVALido"),
            "total_allowed_taxed": self._parse_total(
                move, fact, "TotalFaturaIVACalculado"
            ),
            "oficio": self._parse_oficio(root),
            "completeness_warning": self._completeness_warning(
                root, error_count, problems
            ),
            "generation_error": False,
        }
        result = self._store_result(move, result_vals, lines, errors)
        self._attach_document(move, result, exchange_record)
        # a definitive with-errors result goes straight to its draft note
        # (credit or debit); a failure holds this result only, with reason
        result._generate_note_or_hold()
        return _(
            "SPMS check result of %(invoice)s processed: %(state)s, "
            "%(lines)s lines, %(errors)s errors."
        ) % {
            "invoice": move.display_name,
            "state": check_state,
            "lines": len(lines),
            "errors": error_count,
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

    def _parse_total(self, move, fact, name):
        """The official totals are the money: a missing, empty or
        unparseable amount rejects the whole document."""
        text = _child_text(fact, name)
        if not text:
            raise UserError(
                _("The check document of %(invoice)s carries no %(field)s")
                % {"invoice": move.display_name, "field": name}
            )
        try:
            return float(text.replace(",", "."))
        except ValueError as err:
            raise UserError(
                _(
                    "The check document of %(invoice)s carries an "
                    "unparseable %(field)s: %(text)s"
                )
                % {
                    "invoice": move.display_name,
                    "field": name,
                    "text": _child_text(fact, name),
                }
            ) from err

    def _claim_float(self, claim, name, prescription, problems):
        """Claim amounts only shape the breakdown (the credit-note money
        comes from the official totals): store zero and report, never
        block."""
        try:
            return _child_float(claim, name)
        except ValueError:
            problems.append(
                _(
                    "Unparseable %(field)s of prescription "
                    "%(prescription)s: %(text)s (stored as 0)."
                )
                % {
                    "field": name,
                    "prescription": prescription or "?",
                    "text": _child_text(claim, name),
                }
            )
            return 0.0

    def _parse_lines(self, move, fact, problems):
        """The document's claims as (line values, error values) pairs, in
        document order, plus the errors anchored to the invoice itself,
        which have no line."""
        error_type_model = self.env["spms.invoice.check.error.type"]
        line_by_prescription = {}
        for line in move.invoice_line_ids:
            if line.spms_prescription:
                line_by_prescription.setdefault(line.spms_prescription, [])
                line_by_prescription[line.spms_prescription].append(line.id)

        def errors_of(anchor_element, level, anchor_vals=None):
            errors = []
            for erro in _children(anchor_element, "Erro"):
                message = _child_text(erro, "Mensagem")
                error_type = error_type_model._get_or_create(
                    _child_text(erro, "Codigo"), message
                )
                if not error_type:
                    # unnamed error: left to the completeness warning
                    continue
                errors.append(
                    dict(
                        anchor_vals or {},
                        level=level,
                        error_type_id=error_type.id,
                        description=message,
                    )
                )
            return errors

        lines = []
        errors = errors_of(fact, "invoice")
        for lote in _children(fact, "LoteErrosEDiferencas"):
            lot_vals = {
                "lot_type": _child_text(lote, "TipoLote"),
                "lot_number": _child_text(lote, "Numero"),
            }
            for claim in _children(lote, "PrestacoesErrosEDiferencas"):
                prescription = _child_text(claim, "NumeroPrescricao")
                line_ids = line_by_prescription.get(prescription, [])
                line_vals = dict(
                    lot_vals,
                    prescription=prescription,
                    amount_billed=self._claim_float(
                        claim, "ValorTotalLido", prescription, problems
                    ),
                    amount_allowed=self._claim_float(
                        claim, "ValorTotalCalculado", prescription, problems
                    ),
                    days_billed=self._claim_float(
                        claim, "QuantidadeLida", prescription, problems
                    ),
                    days_paid=self._claim_float(
                        claim, "QuantidadeCalculado", prescription, problems
                    ),
                    move_line_id=line_ids[0] if len(line_ids) == 1 else False,
                )
                line_errors = errors_of(claim, "prestacao")
                for linha in _children(claim, "LinhaPrestacaoErrosEDiferencas"):
                    line_errors.extend(
                        errors_of(
                            linha,
                            "linha",
                            {
                                "provider_system_ref": _child_text(
                                    linha, "SistemaPrescrito"
                                )
                            },
                        )
                    )
                for prescription_data in _children(claim, "PrescricaoErrosEDiferencas"):
                    # the prescription data has lines of its own, flagged
                    # like the claim lines, with the prescribed system
                    for linha in _children(
                        prescription_data, "LinhaPrescricaoErrosEDiferencas"
                    ):
                        line_errors.extend(
                            errors_of(
                                linha,
                                "prescricao",
                                {
                                    "provider_system_ref": _child_text(
                                        linha, "SistemaPrescrito"
                                    )
                                },
                            )
                        )
                    line_errors.extend(errors_of(prescription_data, "prescricao"))
                lines.append((line_vals, line_errors))
        return lines, errors

    def _completeness_warning(self, root, error_count, problems):
        """Any Erro the anchored walk did not turn into an error record,
        and any unparseable claim amount, is reported on the result, never
        blocking."""
        total = sum(1 for element in root.iter() if _local(element.tag) == "Erro")
        missing = total - error_count
        warnings = []
        if missing > 0:
            warnings.append(
                _(
                    "%(count)s of the %(total)s errors of the check document "
                    "sit at positions this parser does not know: the error "
                    "list below is incomplete. The raw document attached to "
                    "this result is the authority."
                )
                % {"count": missing, "total": total}
            )
        warnings.extend(problems)
        return "\n".join(warnings) or False

    def _parse_document_date(self, root, problems):
        text = _child_text(root, "IssueDate")
        if not text:
            return False
        try:
            return fields.Date.to_date(text)
        except ValueError:
            problems.append(
                _("IssueDate %r is not a date: the document date is left empty.") % text
            )
            return False

    def _parse_oficio(self, root):
        response = _find_first(root, "Response")
        return _child_text(response, "Description") if response is not None else False

    def _store_result(self, move, result_vals, lines, errors):
        """Upsert the 1:1 result and rebuild its lines and errors
        (idempotent)."""
        result = move.spms_invoice_check_ids[:1]
        if result and result.official_locked:
            raise UserError(
                _(
                    "The check result of %s is already carried by a live "
                    "credit or debit note; cancel that note before "
                    "reprocessing the document."
                )
                % move.display_name
            )
        if result:
            result.error_ids.unlink()
            result.line_ids.unlink()
            result.write(result_vals)
        else:
            result = self.env["spms.invoice.check"].create(
                dict(result_vals, move_id=move.id)
            )
        if lines:
            self.env["spms.invoice.check.line"].create(
                [
                    dict(
                        line_vals,
                        result_id=result.id,
                        error_ids=[
                            (0, 0, dict(error_vals, result_id=result.id))
                            for error_vals in line_errors
                        ],
                    )
                    for line_vals, line_errors in lines
                ]
            )
        if errors:
            self.env["spms.invoice.check.error"].create(
                [dict(error_vals, result_id=result.id) for error_vals in errors]
            )
        return result

    def _attach_document(self, move, result, exchange_record):
        """One attachment per result: reprocessing replaces its content."""
        name = "%s-spms-check.xml" % (move.name or "invoice").replace("/", "_")
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", result._name),
                ("res_id", "=", result.id),
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
                    res_model=result._name,
                    res_id=result.id,
                )
            )
