import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from infrastructure.search.elasticsearchClient import (
    check_elasticsearch_connection,
    close_elasticsearch_client,
    create_elasticsearch_client,
)
from search.indexManager import ensure_index
from search.schemaLoader import (
    SEARCH_SCHEMA_DIR,
    build_index_name,
    load_search_schema,
)


def main() -> None:
    print("ROOT_DIR:")
    print(ROOT_DIR)

    print("\nSEARCH_SCHEMA_DIR:")
    print(SEARCH_SCHEMA_DIR)

    print("\nExpected schema file:")
    print(SEARCH_SCHEMA_DIR / "watchlist.yaml")

    schema = load_search_schema("watchlist")

    print("\nPhysical index:")
    print(build_index_name(schema))

    payload_properties = (
        schema["mapping"]
        ["properties"]
        ["payload"]
        ["properties"]
    )

    print("\nPOBs loaded from YAML:")
    print("POBs" in payload_properties)

    print("\nDateAdded mapping:")
    print(payload_properties.get("DateAdded"))

    print("\nFullDate mapping:")
    print(
        payload_properties
        .get("Dates", {})
        .get("properties", {})
        .get("FullDate")
    )

    client = create_elasticsearch_client()

    try:
        if not check_elasticsearch_connection(client):
            raise RuntimeError(
                "Could not connect to Elasticsearch."
            )

        print("\nElasticsearch connection: OK")

        index_name = ensure_index(
            client=client,
            schema=schema,
        )

        print(f"Index ready: {index_name}")
        print(
            "Alias ready:",
            schema["index"]["alias"],
        )

    finally:
        close_elasticsearch_client(client)


if __name__ == "__main__":
    main()