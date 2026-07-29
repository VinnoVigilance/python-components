"""Risk Category Engine.

Assigns nominal risk-category labels to mapped watchlist records. Runs as a
post-mapping stage; categories are derived from provenance (which list + why),
driven entirely by data/rules/riskClassification.xlsx.

Modules:
    configLoader         - load + validate the risk-classification workbook
    ruleMatcher          - deterministic ListScope base label + rule layers
    classificationReport - audit/observability report over classified records
"""
