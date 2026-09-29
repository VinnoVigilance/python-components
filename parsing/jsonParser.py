import json


class JsonParser:
    """
    Parse a raw JSON snapshot (one JSON document) into records.

    If the config provides an ``items_path`` (dot-separated), the parser
    descends to it and yields each element of that list as a record -- the
    same role ``items_path`` played for the API collector's ``single_jsonl``
    write mode. Without one, the whole document is yielded as a single record.

    Matches the interface of the other parsers (``parse(file_path, config)``
    yielding dicts) so the rest of the pipeline is unchanged.
    """

    def parse(self, file_path, config):
        with open(file_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)

        items_path = (config or {}).get("items_path")

        if items_path:
            for part in str(items_path).split("."):
                data = data.get(part) if isinstance(data, dict) else None

                if data is None:
                    return

        if isinstance(data, list):
            for item in data:
                yield item
        elif data is not None:
            yield data
