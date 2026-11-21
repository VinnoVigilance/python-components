from ingestion.urlResolver.linkResolver import (
    resolve_by_link_text,
    resolve_json_list_filter,
    resolve_latest_year_page,
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
        options = {
            key: resolver_config[key]
            for key in ("link_selector", "encoding", "timeout", "headers")
            if key in resolver_config
        }
        if "headers" not in options and config.get("download_headers"):
            options["headers"] = config["download_headers"]
        return resolve_by_link_text(
            source_page_url=resolver_config[
                "source_page_url"
            ],
            link_text=resolver_config[
                "value"
            ],
            **options,
        )

    if resolver_type == "json_list_filter":
        return resolve_json_list_filter(
            cfg=resolver_config,
        )

    if resolver_type == "latest_year_page":
        return resolve_latest_year_page(resolver_config)

    raise ValueError(
        f"Unsupported URL resolver type: "
        f"{resolver_type}"
    )
