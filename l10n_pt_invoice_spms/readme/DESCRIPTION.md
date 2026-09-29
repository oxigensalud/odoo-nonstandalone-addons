Generate invoice for SPMS

Once SPMS has accepted an invoice, it can no longer be cancelled or reset to
draft; corrections go through a credit note.

Invoices and credit notes are submitted to the CCF web service (FacturaCRDWS)
with a zeep client built from the WSDL shipped in `api/FacturaCRDWS.wsdl`. The
WSDL SPMS publishes cannot be used as is; the header of that file documents
every correction and its evidence, and the WSDL as served by SPMS on the date
in its name sits next to it for comparison.
