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

        suffix = Path(file_path).suffix.lower()
        sheet_name = config.get("sheet_name", 0)

        if suffix == ".csv":

            df = pd.read_csv(
                file_path,
                dtype=str
            )

        elif suffix in [".xlsx", ".xls"]:

            df = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                dtype=str
            )

        else:

            raise ValueError(
                f"Unsupported file type: {suffix}"
            )

        records = self.dataframe_to_records(df)

        for record in records:
            yield record