import ollama


def generate(prompt, model_name, schema=None):
    response = ollama.chat(
        model=model_name,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        format=schema if schema else "json",
        options={
            "temperature": 0
        }
    )

    return response["message"]["content"]