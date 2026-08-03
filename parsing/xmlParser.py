from lxml import etree
import json
from pathlib import Path


class XmlParser:

    def resolve_root_tags(self, config=None, root_tags=None):
        # Priority:
        # 1. Explicit root_tags argument
        # 2. root_tags from config
        # 3. Default
        if root_tags is not None:
            return root_tags

        if config is not None:
            return config.get("root_tags", ["Designation"])

        return ["Designation"]

    def matches_root_tag(self, elem, root_tags):
        tag = elem.tag

        # Comments and processing instructions carry a callable tag
        if not isinstance(tag, str):
            return False

        return tag.split("}")[-1] in root_tags

    def release(self, elem):
        # Free the subtree we just consumed along with any siblings
        # already processed. Only called once a matched element has
        # been converted, so this never drops data still to be read.
        elem.clear()

        parent = elem.getparent()

        if parent is None:
            return

        while elem.getprevious() is not None:
            del parent[0]

    def elem_to_dict(self, elem):

        node = {}

        for attr_key, attr_value in elem.attrib.items():
            node[attr_key] = attr_value

        # Comments and processing instructions carry a callable tag
        # rather than a string, and are not part of the data
        children = [
            child for child in elem
            if isinstance(child.tag, str)
        ]

        if not children:

            text = (elem.text or "").strip()

            if not node:
                return text

            if text:
                node["text"] = text

            return node

        for child in children:

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
        root_tags = self.resolve_root_tags(config, root_tags)

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

                if self.matches_root_tag(elem, root_tags):
                    data = self.elem_to_dict(elem)
                    data["_root_tag"] = elem.tag.split("}")[-1]

                    f.write(
                        json.dumps(
                            data,
                            ensure_ascii=False
                        ) + "\n"
                    )

                    count += 1
                    self.release(elem)

        print(f"Done: {count}")

        return output_file

    def parse(
        self,
        file_path,
        config=None,
        root_tags=None
    ):
        root_tags = self.resolve_root_tags(config, root_tags)

        context = etree.iterparse(
            file_path,
            events=("end",),
            recover=True
        )

        for _, elem in context:

            if self.matches_root_tag(elem, root_tags):

                data = self.elem_to_dict(elem)
                data["_root_tag"] = elem.tag.split("}")[-1]

                self.release(elem)

                yield data


if __name__ == "__main__":

    parser = XmlParser()

    output_file = parser.run_xml_ingestion(
        xml_file="/Users/mac/Desktop/VV_Python_Project/20260430-FULL-1_1(xsd).xml",
        output_file="EU_full.jsonl",
        root_tags=["sanctionEntity"]
    )

    print(f"Output: {output_file}")