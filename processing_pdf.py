import os
import shutil

from pdf2image import convert_from_path


def create_img_dir():
    shutil.rmtree("img/")
    directory_name = "img"
    try:
        os.mkdir(directory_name)
    except FileExistsError:
        print(f"Directory '{directory_name}' already exists.")
    except PermissionError:
        print(f"Permission denied: Unable to create '{directory_name}'.")
    except Exception as e:
        print(f"An error occurred: {e}")


def convert(pdf_file):
    print("Creando directorio")

    create_img_dir()

    print("Convirtiendo")

    convert_from_path(
        pdf_file,
        output_folder="img/",
        fmt="jpeg",
        jpegopt={"quality": 70, "optimize": True, "progressive": False},
        output_file=pdf_file.removesuffix(".pdf").removeprefix("docs/"),
    )
