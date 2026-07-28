Atlas GUI Autonomous Dashboard Patch

Changes:
- Integrates the portfolio read model into DashboardSnapshot.
- Adds portfolio metric cards to the dashboard.
- Removes GUI paper-order entry and trading-service dependencies.
- Keeps Orders as a read-only monitoring page.
- Renames navigation toward AI/Diagnostics supervision.
- Keeps Start, Stop, and Emergency Stop runtime controls.

Prerequisite:
- app/read_models/portfolio must already exist with project_portfolio_read_model().

Apply from repository root:
1. Extract this ZIP.
2. Run:
   Set-ExecutionPolicy -Scope Process Bypass
   .\Atlas_GUI_Autonomous_Dashboard_Patch\Apply-AtlasGuiPatch.ps1
3. Validate:
   python -m pytest
   python -m app.gui.app
