WITH members AS (
    SELECT wm.full_payload AS p,
           wm.full_payload -> 'Sources' -> 0 ->> 'ListName' AS list_name
    FROM core.watchlist_member wm
    WHERE wm.is_current
),
measures AS (
    SELECT m.list_name, x
    FROM members m
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(m.p -> 'Measures') = 'array' THEN m.p -> 'Measures' ELSE '[]'::jsonb END) AS x
),
programs AS (
    SELECT m.list_name, x
    FROM members m
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(m.p -> 'Programs') = 'array' THEN m.p -> 'Programs' ELSE '[]'::jsonb END) AS x
)
SELECT DISTINCT 'list' AS kind, list_name, '' AS type, '' AS value
FROM members
UNION
SELECT DISTINCT 'measure', list_name, coalesce(x ->> 'MeasureType', ''),
       CASE WHEN list_name = 'DMW-RECRUITMENT-AGENCIES'
            THEN trim(substring(x ->> 'Note' FROM 'License Status:\s*(.*)')) ELSE '' END
FROM measures
UNION
SELECT DISTINCT 'program', list_name, coalesce(x ->> 'ProgramType', ''), coalesce(x ->> 'Program', '')
FROM programs
WHERE x ->> 'ProgramType' IN ('Sanctions', 'Counter-Terrorism') OR list_name = 'FBI-WANTED'
ORDER BY kind, list_name, type, value;
