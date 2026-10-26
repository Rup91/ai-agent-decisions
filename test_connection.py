import os
from dotenv import load_dotenv
from google import genai
from qdrant_client import QdrantClient
load_dotenv()

def test_gemini():
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    response = client.models.embed_content(
        model=os.getenv("GEMINI_EMBEDDING_MODEL",
                        "gemini-embedding-001"),
                        contents=["Project status test"],
                        config={"task_type": "RETRIEVAL_DOCUMENT","output_dimensionality": 
                                int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "3072")),
                                },)
    print("Gemini: SUCCESS")
    print("Embedding dimensions:", len(response.embeddings[0].values))

def test_qdrant():
    client = QdrantClient(url=os.getenv("QDRANT_URL"),api_key=os.getenv("QDRANT_API_KEY"),)
    print("Qdrant: SUCCESS")
    print("Existing collections:", [collection.name for collection in client.get_collections().collections ])

    client.close()

if __name__ == "__main__":
    test_gemini()
    test_qdrant()
