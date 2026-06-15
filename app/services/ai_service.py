# app/services/ai_service.py
import urllib.request
import json
import logging
from typing import Union, List, Dict, Any
from app.services.sync_service import load_config

logger = logging.getLogger("ai_service")

class AIService:
    def call_ai_api(self, system_prompt: str, messages_or_prompt: Union[str, List[Dict[str, str]]], temperature: float = 0.7) -> str:
        """Realiza la llamada HTTP a la API de Gemini o DeepSeek según la configuración local."""
        cfg = load_config()
        provider = cfg.get("active_ai_provider", "deepseek").strip().lower()

        if provider == "gemini":
            api_key = cfg.get("gemini_api_key", "").strip()
            if not api_key:
                raise ValueError("No se ha configurado la clave de API de Gemini. Configúrala en Ajustes.")
            
            model = cfg.get("gemini_model", "gemini-flash-latest").strip()
            # Normalizaciones de modelo
            if model == "gemini-1.5-flash":
                model = "gemini-flash-latest"
            elif model == "gemini-1.5-pro":
                model = "gemini-pro-latest"
            elif model == "gemini-2.0-flash-exp":
                model = "gemini-2.0-flash"

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            
            contents = []
            if isinstance(messages_or_prompt, str):
                contents.append({
                    "role": "user",
                    "parts": [{"text": messages_or_prompt}]
                })
            else:
                for msg in messages_or_prompt:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if role == "system":
                        continue
                    gemini_role = "user" if role == "user" else "model"
                    contents.append({
                        "role": gemini_role,
                        "parts": [{"text": content}]
                    })
            
            data = {
                "contents": contents,
                "generationConfig": {
                    "temperature": temperature
                }
            }
            if system_prompt:
                data["systemInstruction"] = {
                    "parts": [{"text": system_prompt}]
                }
                
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=45) as response:
                res_data = response.read().decode("utf-8")
                res_json = json.loads(res_data)
                
                if "candidates" not in res_json or not res_json["candidates"]:
                    if "error" in res_json:
                        raise Exception(res_json["error"].get("message", "Error de Gemini"))
                    raise Exception("La API de Gemini no devolvió respuestas válidas.")
                
                content = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                return content

        else:  # deepseek / openrouter
            api_key = cfg.get("deepseek_api_key", "").strip()
            if not api_key:
                raise ValueError("No se ha configurado la clave de API de DeepSeek. Configúrala en Ajustes.")
                
            if api_key.startswith("sk-or-v1-"):
                url = "https://openrouter.ai/api/v1/chat/completions"
                model = "deepseek/deepseek-chat"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/sahid8v/launchpad",
                    "X-Title": "Launchpad Notes App",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                }
            else:
                url = "https://api.deepseek.com/chat/completions"
                model = "deepseek-chat"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                }
                
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
                
            if isinstance(messages_or_prompt, str):
                messages.append({"role": "user", "content": messages_or_prompt})
            else:
                for msg in messages_or_prompt:
                    if msg.get("role") != "system":
                        messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
                        
            data = {
                "model": model,
                "messages": messages,
                "temperature": temperature
            }
            
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=45) as response:
                res_data = response.read().decode("utf-8")
                res_json = json.loads(res_data)
                
                if "choices" not in res_json or not res_json["choices"]:
                    if "error" in res_json:
                        raise Exception(res_json["error"].get("message", "Error de DeepSeek"))
                    raise Exception("La API de DeepSeek no devolvió respuestas válidas.")
                    
                content = res_json["choices"][0]["message"]["content"].strip()
                return content

    def chat_with_assistant(self, history: List[Dict[str, str]], user_message: str) -> str:
        """Maneja la conversación general con el Asistente."""
        system_prompt = (
            "Eres un asistente general inteligente integrado en una aplicación de notas y mapas mentales. "
            "Ayuda al usuario a resumir información, organizar sus pensamientos y responder sus preguntas de "
            "forma clara y concisa en formato Markdown."
        )
        
        messages = []
        # Mantener solo las últimas 10 interacciones del historial
        for msg in history[-10:]:
            if "role" in msg and "content" in msg:
                messages.append({"role": msg["role"], "content": msg["content"]})
                
        messages.append({"role": "user", "content": user_message})
        return self.call_ai_api(system_prompt, messages, temperature=0.7)

    def run_copilot_action(self, action: str, text: str) -> str:
        """Maneja las acciones predefinidas del Copilot (resumir, traducir, etc.)."""
        prompts = {
            "summarize": "Eres un asistente de redacción experto. Tu tarea es resumir el siguiente texto de forma concisa y clara en formato Markdown.",
            "improve": "Eres un asistente de redacción experto. Tu tarea es mejorar la redacción, el estilo y la fluidez del siguiente texto, corrigiendo cualquier problema. Devuelve únicamente el texto mejorado en formato Markdown, sin explicaciones ni rodeos.",
            "table": "Eres un asistente de datos experto. Tu tarea es convertir la información y los datos del siguiente texto en una tabla de Markdown bien formateada y limpia. Devuelve únicamente la tabla de Markdown, sin explicaciones ni comentarios.",
            "spelling": "Eres un corrector ortográfico experto. Tu tarea es corregir todos los errores ortográficos y gramaticales del siguiente texto, manteniendo el formato original. Devuelve únicamente el texto corregido, sin explicaciones.",
            "translate": "Eres un traductor experto. Tu tarea es traducir de forma natural el siguiente texto al idioma inglés. Devuelve únicamente la traducción, sin explicaciones ni comentarios."
        }
        system_prompt = prompts.get(action, "Eres un asistente de redacción experto.")
        return self.call_ai_api(system_prompt, text, temperature=0.3)
