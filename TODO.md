- [ ] Langugae (Fluent)
- [ ] General a11y of app
- [ ] Colour contrast
- [ ] Check modals manually
- [ ] Focus indicator
- [ ] Integrate dictiphone

    Yes, absolutely. Claude Code sessions persist — you can close the terminal
    or press Ctrl+C and come back later. When you resume, the conversation      
    context will be compressed but the key information will be retained.        
                                                                                
    The git commits are already saved on your translation-checks branch, so all 
    progress is durable:                                                        

    8f00f01 Remove gettext artifacts and complete Fluent migration cleanup
    8b96dff Rewrite translation validation for Fluent FTL format
    54b6851 Migrate issue descriptions and WCAG labels to Fluent FTL
    57b63d0 Complete JavaScript translation migration to Fluent
    027426b Convert remaining literal _() calls that migration script missed
    75878d7 Add bulk migration script for .po to .ftl conversion
    2499cda Add Fluent FTL files for auth and convert login page as proof of
    concept
    2a56a15 Add Flask-Fluent integration layer and tests
    f74faf8 Add fluent-compiler dependency for Fluent migration

    What's left when you come back:
    - Fix FTL parse errors (Junk entries in common.ftl, issues.ftl,
    recordings.ftl) — a subagent was just dispatched for this but may not have
    completed
    - Get tests/test_translations.py passing (3 failures due to the parse
    errors)
    - ~31 remaining dynamic _() calls in templates that still use Flask-Babel
    (enum value translations)
    - The proposal doc (docs/FLUENT_MIGRATION_PROPOSAL.md) and plan
    (docs/superpowers/plans/2026-04-10-fluent-migration.md) are uncommitted but
    on disk
