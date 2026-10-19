from ingestion.urlResolver.linkResolver import (
    resolve_by_link_text,
    resolve_json_list_filter,
)


def resolve_url(config):
    resolver_config = config.get(
        "url_resolver"
    )

    # Sources that already have a static URL
    if not resolver_config:
        return config["url"]

    resolver_type = resolver_config["type"]

    if resolver_type == "link_text":
        return resolve_by_link_text(
            source_page_url=resolver_config[
                "source_page_url"
            ],
            link_text=resolver_config[
                "value"
            ],
        )

    if resolver_type == "json_list_filter":
        return resolve_json_list_filter(
            cfg=resolver_config,
        )

    raise ValueError(
        f"Unsupported URL resolver type: "
        f"{resolver_type}"
    )