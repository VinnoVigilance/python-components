-- Register the dated PNP snapshot. Run explicitly against the intended database.
-- Re-running preserves existing source/list metadata. No membership is written.
BEGIN;

INSERT INTO common.lkup_source (name, country, authority, base_url)
VALUES ('PNP', 'Philippines', 'Philippine National Police', 'https://pnp.gov.ph')
ON CONFLICT (name) DO NOTHING;

INSERT INTO common.lkup_source_list_type (source_id, name, code, description)
SELECT id,
       'PNP_MOST_WANTED_WITH_REWARDS_CRIMINALITY_2022',
       'PNP_MOST_WANTED_WITH_REWARDS_CRIMINALITY_2022',
       'PNP Most Wanted Persons with Rewards - Criminality. Historical snapshot dated 13 October 2022; hosted by SPD NCRPO.'
FROM common.lkup_source
WHERE name = 'PNP'
  AND NOT EXISTS (
      SELECT 1 FROM common.lkup_source_list_type
      WHERE source_id = common.lkup_source.id
        AND name = 'PNP_MOST_WANTED_WITH_REWARDS_CRIMINALITY_2022'
  );

COMMIT;
