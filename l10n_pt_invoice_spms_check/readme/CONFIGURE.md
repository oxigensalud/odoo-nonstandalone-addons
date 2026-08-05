- Assign the *SPMS / Consultation* group to users who can read the
  check results, and *SPMS / Responsible* to the users allowed to work
  with them and with the generated credit notes.
- The polling scheduled action (*SPMS: obtain check results*) is
  shipped deactivated because it calls the production CCF web service:
  activate it in the production database once the SPMS credentials are
  in place. Its *Active* field is also the pause switch — never
  uninstall the module just to stop the calls.
- Set the *SPMS Adjustment Line Product* on the company (SPMS page, visible
  to *Technical Settings* users): a sale service product required to append
  the adjustment line when an official value differs from the itemised
  total. Credit notes that need it are skipped with a clear message until
  it is configured.
