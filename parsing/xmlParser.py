from lxml import etree
import json
from pathlib import Path


class XmlParser:

    def elem_to_dict(self, elem):

        node = {}

        for attr_key, attr_value in elem.attrib.items():
            node[attr_key] = attr_value

        if len(elem) == 0:

            text = (elem.text or "").strip()

            if not node:
                return text

            if text:
                node["text"] = text

            return node

        for child in elem:

            tag = child.tag.split("}")[-1]

            value = self.elem_to_dict(child)

            if tag in node:

                if not isinstance(node[tag], list):
                    node[tag] = [node[tag]]

                node[tag].append(value)

            else:
                node[tag] = value

        return node

    def run_xml_ingestion(
        self,
        xml_file,
        output_file=None,
        config=None,
        root_tags=None
    ):
        # Priority:
        # 1. Explicit root_tags argument
        # 2. root_tags from config
        # 3. Default
        if root_tags is None:
            if config is not None:
                root_tags = config.get("root_tags", ["Designation"])
            else:
                root_tags = ["Designation"]

        if output_file is None:

            output_file = (
                f"{Path(xml_file).stem}_raw.jsonl"
            )

        count = 0

        context = etree.iterparse(
            xml_file,
            events=("end",),
            recover=True
        )

        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as f:

            for _, elem in context:

                if any(elem.tag.endswith(tag) for tag in root_tags):
                    data = self.elem_to_dict(elem)

                    f.write(
                        json.dumps(
                            data,
                            ensure_ascii=False
                        ) + "\n"
                    )

                    count += 1
                    elem.clear()

        print(f"Done: {count}")

        return output_file

    def parse(
        self,
        file_path,
        config=None,
        root_tags=None
    ):
        # Priority:
        # 1. Explicit root_tags argument
        # 2. root_tags from config
        # 3. Default
        if root_tags is None:
            if config is not None:
                root_tags = config.get("root_tags", ["Designation"])
            else:
                root_tags = ["Designation"]

        context = etree.iterparse(
            file_path,
            events=("end",),
            recover=True
        )

        for _, elem in context:

            if any(elem.tag.endswith(tag) for tag in root_tags):

                data = self.elem_to_dict(elem)

                elem.clear()

                yield data


if __name__ == "__main__":

    parser = XmlParser()

    output_file = parser.run_xml_ingestion(
        xml_file="/Users/mac/Desktop/VV_Python_Project/20260430-FULL-1_1(xsd).xml",
        output_file="EU_full.jsonl",
        root_tags=["sanctionEntity"]
    )

    print(f"Output: {output_file}")