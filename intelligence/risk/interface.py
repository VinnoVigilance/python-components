"""Select a classifier without importing the other domain's dependencies."""


def create_classifier(subject, model_path=None, threads=4, model_enabled=True):
    if subject == "media":
        from intelligence.risk.mediaRiskClassifier import MediaRiskClassifier
        return MediaRiskClassifier(model_path)
    if subject == "watchlist":
        from intelligence.risk.watchlistRiskClassifier import WatchlistRiskClassifier
        return WatchlistRiskClassifier(model_path, threads, model_enabled=model_enabled)
    raise ValueError("subject must be media or watchlist.")
