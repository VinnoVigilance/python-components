import hashlib

def calculate_file_hash(file_path):

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def calculate_record_hash(record_path):
    if record_path is None:
        return None

    return hashlib.sha256(
        str(record_path).encode("utf-8")
    ).hexdigest()