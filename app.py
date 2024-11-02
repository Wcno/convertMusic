import os
from flask import Flask, render_template, request, send_file
import yt_dlp as youtube_dl

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/convert", methods=["POST"])
def convert_youtube_to_mp3():
    url = request.form.get("url")
    if not url:
        return render_template("index.html", error="URL no proporcionada")

    try:
        output_folder = "mp3_files"
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
        
        # Usar yt-dlp para descargar y convertir a MP3
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(output_folder, '%(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
        
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Buscar el archivo descargado
        for file in os.listdir(output_folder):
            if file.endswith(".mp3"):
                mp3_file = os.path.join(output_folder, file)
                break

        return send_file(mp3_file, as_attachment=True)

    except Exception as e:
        return render_template("index.html", error=f"Error al convertir: {e}")

if __name__ == "__main__":
    app.run(debug=True)
#update 2024