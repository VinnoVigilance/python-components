import pandas as pd
from pathlib import Path


class TabularParser:

    def dataframe_to_records(self, df):

        return (
            df.fillna("")
            .astype(str)
            .to_dict(orient="records")
        )

    def parse(self, file_path, config):

        file_type = str(config.get("file_type", "")).strip().lower()
        sheet_name = config.get("sheet_name", 0)

        if file_type == "csv":
            df = pd.read_csv(file_path, dtype=str)
        elif file_type in ("xlsx", "xls"):
            df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")

        records = self.dataframe_to_records(df)

        for record in records:
            yield record