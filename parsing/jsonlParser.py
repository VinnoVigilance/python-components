import json


class JsonlParser:
    """
    Parse a raw JSONL snapshot into records.

    Each line is one JSON object (one record, verbatim from the source API).
    Yields dicts, matching the interface of the other parsers so the rest of
    the pipeline is unchanged.
    """

    def parse(self, file_path, config):
        with open(file_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()

                if not line:
                    continue

                yield json.loads(line)
