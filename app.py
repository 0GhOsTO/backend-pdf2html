#apt-get intall -y poppler utils
#pip install pdf2image
#pip install boto3

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from werkzeug.utils import secure_filename
import boto3
import re
import fitz

jobs = {} 

aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
region_name = os.environ.get("AWS_REGION")
bucket_name = os.environ.get("BUCKET_NAME")

#Initialize Flask
app = Flask(__name__)
CORS(app, origins=["https://frontend-pdf2html.vercel.app", "https://frontend-pdf2html-git-master-andrew-chos-projects-415cbbd8.vercel.app/", "https://frontend-pdf2html-fskhd9ppm-andrew-chos-projects-415cbbd8.vercel.app/"])
print("CORS accepted")

#Testing: http://localhost:5173/
#Reality: https://frontend-pdf2html.vercel.app/

#Make the file uploadable
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route("/upload", methods=["POST"])
def upload_pdf():
    print("Origin header received:", request.headers.get("Origin"))
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    html_content = process_pdf(filepath)
    jsoned = jsonify({"html": html_content})
    print("jsoned:", jsoned)
    return jsoned, 202


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel_job(job_id):
    if job_id in jobs:
        jobs[job_id]["cancel"] = True
        return jsonify({"status": "cancelled"})
    else:
        return jsonify({"error": "Invalid job ID"}), 400

@app.route("/result/<job_id>")
def get_result(job_id):
    if job_id in jobs:
        result = jobs[job_id]["result"]
        if result is not None:
            return jsonify({"html": result, "status": "done"})
        else:
            return jsonify({"status": "processing"})
    else:
        return jsonify({"error": "Invalid job ID"}), 400


def cellText(cell, block_map):
    text = ""
    if "Relationships" in cell:
        for rel in cell["Relationships"]:
            if rel["Type"] == "CHILD":
                for childID in rel["Ids"]:
                    word = block_map[childID]
                    if word["BlockType"] == "WORD":
                        text += word["Text"] + " "
    return text.strip()

def process_pdf(pdf_path):
    print("Running pdf_path right now: ")
    
    pages_dir = "pages"
    os.makedirs(pages_dir, exist_ok = True)

    doc = fitz.open(pdf_path)
    image_files = []
    for i in range(len(doc)):
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=200)  # dpi can be adjusted (default: 72)
        img_path = os.path.join(pages_dir, f"page{i}.jpg")
        pix.save(img_path)
        image_files.append(img_path)

    print("image_files: ", image_files)
    print("Converted into image")

    # 2. Upload images to S3
    s3 = boto3.client(
        "s3",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=region_name
    )

    files = []
    for img_file in image_files:
        file_name = os.path.basename(img_file)
        files.append(file_name)
        s3.upload_file(img_file, bucket_name, file_name)

    print("files: ", files)
    print("Uploaded to s3 with image files")

    # 3. Process each image with Textract (replace with your own logic as needed)
    textract = boto3.client(
        "textract",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=region_name
    )

    print("textract called")

    content = ""
    htmlTableList = {}
    paragraph = [""]
    paragraphIndex = 0
    header = False

    print("Empty htmlTable, paragraph Created")

    # Sort the file names in order: page0.jpg, page1.jpg, ...
    # files = sorted([os.path.basename(p) for p in image_files], key=lambda x: int(re.findall(r'\d+', x)[0]))

    #####################Testing start: 
    # pages_dir = "pages"
    # files = [
    #     os.path.join(pages_dir, f)
    #     for f in os.listdir(pages_dir)
    #     if f.endswith(".jpg")
    # ]
    # print("files: ", files)

    # aws_access_key_id = "???"
    # aws_secret_access_key = "???"
    # region_name = "???"
    # bucket_name = "???"

    # textract = boto3.client(
    #     "textract",
    #     aws_access_key_id=aws_access_key_id,
    #     aws_secret_access_key=aws_secret_access_key,
    #     region_name=region_name
    # )

    # def cellText(cell, block_map):
    #     text = ""
    #     if "Relationships" in cell:
    #         for rel in cell["Relationships"]:
    #             if rel["Type"] == "CHILD":
    #                 for childID in rel["Ids"]:
    #                     word = block_map[childID]
    #                     if word["BlockType"] == "WORD":
    #                         text += word["Text"] + " "
    #     return text.strip()
    #####################Testing end

    for file in files:
        # Get Textract analysis
        print("Running textract on: ", file)

        layoutAnalysis = textract.analyze_document(
            Document={"S3Object": {"Bucket": bucket_name, "Name": file}},
            FeatureTypes=["TABLES", "FORMS"]
        )

        print("TEXTRACT analysis complete", file)

        #Block map creationg
        block_map = {blk["Id"]: blk for blk in layoutAnalysis["Blocks"]}
        tableList = {}
        for block in layoutAnalysis["Blocks"]:
            if block["BlockType"] == "TABLE":
                boundingBox = block["Geometry"]["BoundingBox"]
                tableList[block["Id"]] = [
                    boundingBox["Width"], boundingBox["Height"],
                    boundingBox["Left"], boundingBox["Top"]
                ]
        print("Block map created: ", block_map)
        print("Table Detected: ", tableList)

        # -- Table Extraction Logic--
        for block in layoutAnalysis["Blocks"]:
            if block["Geometry"]["BoundingBox"]["Top"] > 0.95:
                continue
            maxTableRow, maxTableCol = -1, -1
            htmlTable = ""
            if block["BlockType"] == "TABLE":
                table_id = block["Id"]
                cells = []
                for child_id in block["Relationships"][0]["Ids"]:
                    cell = block_map[child_id]
                    if cell["BlockType"] == "CELL":
                        row, col = cell["RowIndex"], cell["ColumnIndex"]
                        text = cellText(cell, block_map)
                        cells.append((row, col, text))
                        maxTableRow = max(maxTableRow, row)
                        maxTableCol = max(maxTableCol, col)
                htmlTable += "<table>"
                tableArr = [[None] * maxTableCol for _ in range(maxTableRow)]
                for i in cells:
                    tableArr[i[0] - 1][i[1] - 1] = i[2]
                for i in tableArr:
                    htmlTable += "<tr>"
                    for j in i:
                        if j == "":
                            htmlTable += '<td><input type="text"></td>'
                        else:
                            htmlTable += "<td>" + (j or "") + "</td>"
                    htmlTable += "</tr>"
                htmlTable += "</table>"
                htmlTableList[table_id] = htmlTable

            # Paragraph & header logic
            left = block["Geometry"]["BoundingBox"]["Left"]
            top = block["Geometry"]["BoundingBox"]["Top"]
            inTable = False
            for table in tableList:
                coord = block_map[table]["Geometry"]["BoundingBox"]
                if (coord["Left"] < left < coord["Left"] + coord["Width"]) and (coord["Top"] < top < coord["Top"] + coord["Height"]):
                    if table not in paragraph:
                        paragraph.append(table)
                    inTable = True
            if (
                not inTable
                and block["BlockType"] == "LINE"
                and not (0.03 <= block["Geometry"]["BoundingBox"]["Left"] <= 0.04)
            ):
                text = block["Text"]
                if block["BlockType"] != "TABLE":
                    if 0.11 <= block["Geometry"]["BoundingBox"]["Left"]:
                        if header:
                            header = False
                        else:
                            paragraphIndex += 1
                        paragraph.append("")
                        paragraph[paragraphIndex] = paragraph[paragraphIndex] + text
                    else:
                        if re.match(r"^\d+\.$", block["Text"]):
                            paragraph.append("")
                            paragraphIndex += 1
                        paragraph[paragraphIndex] = paragraph[paragraphIndex] + text
                    if re.match(r"^\d+\.$", block["Text"]) or re.match(r"^\d+\.+\d+\.$", block["Text"]):
                        header = True
            else:
                inTable = False
        
        print("htmlTable created: ", htmlTableList)
        print("Paragraph length: ", len(paragraph))
        print("Paragraph: ", paragraph)

    # Unwrapping paragraphs and adding tables
    for text in paragraph:
        if text in htmlTableList:
            content += htmlTableList[text]
        else:
            content += f"<p>{text}</p>\n"

    # --- Convert content to full HTML ---
    htmlConvert = "<!DOCTYPE html>\n"
    htmlConvert += "<head>\n"
    htmlConvert += "<style>table{ border-collapse: collapse; margin: 20px auto; } th, td {border: 1px solid #333;padding: 8px 12px;text-align: center;}th{background-color: #f2f2f2;}</style>"
    htmlConvert += "</head>\n"
    htmlConvert += '<html lang="en">\n'
    htmlConvert += "<body>\n"
    htmlConvert += content + "\n"
    htmlConvert += "</body>\n"
    htmlConvert += "<footer></footer>\n"
    htmlConvert += "</html>\n"

    print("final: ", htmlConvert)
    return htmlConvert

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")










