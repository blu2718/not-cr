import json


def to_md(json_output, name):
    with open(json_output, "r") as file:
        output = json.load(file)

    content = output["choices"][0]["message"]["content"]
    content_clean = content.removeprefix("```markdown\n").removesuffix("```")

    print("└ Guardando a Markdown")

    with open(f"output/{name}.md", "w", encoding="utf-8") as f:
        f.write(content_clean)
