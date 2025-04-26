# main.py (v1.0.3 - Busca Direta de Slots + Correção GPT Call)
import re
import os
import logging
import time
import datetime
import pytz
import locale # Para formatar datas/horas em PT-BR
import json # Para debug
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import Response
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from typing import Dict, List, Optional, Any, Tuple
from dateutil.relativedelta import relativedelta  # Para manipulação de datas com meses
from dateparser import parse as parse_date

# Importa nossos handlers atualizados
# Certifique-se que caldav_handler.py foi atualizado para aceitar patient_email
from openai_handler import OpenAIHandler
from knowledge_handler import KnowledgeHandler
from caldav_handler import CaldavHandler # ASSUME QUE FOI CORRIGIDO

logger = logging.getLogger(__name__)

# --- Configuração Inicial ---
load_dotenv()

# Configuração de Logging (mais robusta)
log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s' # Adiciona módulo e linha
logging.basicConfig(level=logging.DEBUG if os.getenv("DEBUG") else log_level_str, format=log_format) # Nível DEBUG se DEBUG=true
logging.getLogger("urllib3").setLevel(logging.WARNING) # Reduz verbosidade de libs externas
logging.getLogger("caldav").setLevel(logging.INFO) # Ajuste conforme necessidade
logging.getLogger("dateparser").setLevel(logging.WARNING) # Reduz verbosidade do dateparser

# --- Variáveis de Ambiente e Configurações ---
try:
    logger.info("Carregando variáveis de ambiente...")
    twilio_account_sid = os.environ['TWILIO_ACCOUNT_SID']
    twilio_auth_token = os.environ['TWILIO_AUTH_TOKEN']
    twilio_whatsapp_number = os.environ['TWILIO_WHATSAPP_NUMBER']
    openai_api_key = os.environ['OPENAI_API_KEY']
    caldav_url = os.environ['CALDAV_URL']
    caldav_username = os.environ['CALDAV_USERNAME']
    caldav_password = os.environ['CALDAV_PASSWORD']
    caldav_calendar_name = os.environ['CALDAV_CALENDAR_NAME']
    margot_persona_name = os.getenv("MARGOT_PERSONA_NAME", "Margot")
    clinic_name = os.getenv("CLINIC_NAME", "Clínica Missel")
    test_destination_number = os.getenv("TEST_DESTINATION_NUMBER") # Para testes manuais se necessário
    default_timezone = os.getenv("DEFAULT_TIMEZONE", "America/Sao_Paulo")

    required_vars = {
        "TWILIO_ACCOUNT_SID": twilio_account_sid, "TWILIO_AUTH_TOKEN": twilio_auth_token,
        "TWILIO_WHATSAPP_NUMBER": twilio_whatsapp_number, "OPENAI_API_KEY": openai_api_key,
        "CALDAV_URL": caldav_url, "CALDAV_USERNAME": caldav_username,
        "CALDAV_PASSWORD": caldav_password, "CALDAV_CALENDAR_NAME": caldav_calendar_name,
    }
    missing_vars = [key for key, value in required_vars.items() if not value]
    if missing_vars:
        raise ValueError(f"Variáveis de ambiente essenciais faltando: {', '.join(missing_vars)}")

    # Configuração do Locale PT-BR (essencial para formatação de datas)
    try:
        locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
        logger.info("Locale 'pt_BR.UTF-8' configurado para formatação de datas.")
    except locale.Error:
        try:
            locale.setlocale(locale.LC_TIME, 'Portuguese_Brazil.1252') # Fallback para Windows
            logger.info("Locale 'Portuguese_Brazil.1252' configurado para formatação de datas.")
        except locale.Error:
            logger.warning("Não foi possível configurar locale pt_BR. Datas podem usar formato padrão.")

    # Valida o timezone
    try:
        pytz.timezone(default_timezone)
        logger.info(f"Timezone padrão configurado para: {default_timezone}")
    except pytz.UnknownTimeZoneError:
        logger.critical(f"Timezone inválido '{default_timezone}' em .env! Verifique a lista de timezones do pytz.")
        exit(1)

    logger.info("Variáveis de ambiente carregadas e validadas.")

except (KeyError, ValueError) as e:
    logger.critical(f"Erro Crítico na configuração inicial: {e}. Verifique o arquivo .env ou as variáveis de ambiente.", exc_info=True)
    exit(1)
except Exception as e:
    logger.critical(f"Erro crítico inesperado durante a configuração inicial: {e}", exc_info=True)
    exit(1)

# --- Inicialização de Clientes e Handlers ---
try:
    logger.info("Inicializando cliente Twilio...")
    twilio_client = TwilioClient(twilio_account_sid, twilio_auth_token)
    logger.info("Cliente Twilio inicializado.")

    logger.info("Inicializando Handler OpenAI...")
    openai_handler = OpenAIHandler(api_key=openai_api_key)
    logger.info("Handler OpenAI inicializado.")

    logger.info("Inicializando Handler da Base de Conhecimento...")
    knowledge_handler = KnowledgeHandler(json_file_path="knowledge_base.json")
    if not knowledge_handler.data:
        raise RuntimeError("Base de conhecimento (knowledge_base.json) não encontrada ou inválida.")
    logger.info("Handler da Base de Conhecimento inicializado.")

    logger.info("Inicializando Handler CalDAV...")
    caldav_handler = CaldavHandler(
        url=caldav_url,
        username=caldav_username,
        password=caldav_password,
        calendar_name=caldav_calendar_name
    )
    logger.info("Handler CalDAV inicializado e conectado.")

except (RuntimeError, ConnectionError, ValueError) as e: # Captura erros específicos de inicialização
    logger.critical(f"Falha CRÍTICA ao inicializar handlers: {e}", exc_info=True)
    exit(1)
except Exception as e: # Captura qualquer outro erro inesperado
    logger.critical(f"Erro CRÍTICO inesperado durante a inicialização: {e}", exc_info=True)
    exit(1)

# --- Criação da Aplicação FastAPI ---
app = FastAPI(
    title="Margot Clinic Assistant API",
    description=f"API para gerenciar interações via WhatsApp com a assistente Margot ({clinic_name}).",
    version="1.0.3" # Versão com busca direta de slots e correção GPT call
)
logger.info("Aplicação FastAPI criada.")

# --- Gerenciamento de Conversa e Estados ---
conversation_sessions: Dict[str, Dict[str, Any]] = {}
MAX_HISTORY_LENGTH = 15
DEFAULT_SESSION_STATE = {
    "history": [],
    "scheduling_status": None, # Controla o fluxo principal
    "patient_data": {}, # Guarda nome, tel, email, proc, indic
    "suggested_slots": [], # Lista de datetimes dos slots sugeridos
    "chosen_slot": None, # Datetime do slot escolhido pelo usuário
    "event_to_modify": None, # Guarda detalhes do evento para cancelar/reagendar {id, summary, start, end}
    "multiple_events_found": [], # Lista de eventos se mais de um for encontrado para cancelar/reagendar
}

# --- Funções Helper ---

def get_session(sender_id: str) -> Dict[str, Any]:
    """ Obtém a sessão do usuário ou inicializa uma nova. """
    if sender_id not in conversation_sessions:
        import copy
        conversation_sessions[sender_id] = copy.deepcopy(DEFAULT_SESSION_STATE)
        logger.info(f"Nova sessão criada para {sender_id}")
        # Recupera dados do paciente se existirem
        try:
            import redis
            redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
            redis_key = f"memoria:{sender_id}"
            if redis_client.exists(redis_key):
                saved_data = redis_client.hgetall(redis_key)
                if saved_data:
                    conversation_sessions[sender_id]["patient_data"].update(saved_data)
                    logger.info(f"[{sender_id}] Dados anteriores do paciente carregados da memória Redis.")
        except Exception as e_redis_load:
            logger.error(f"[{sender_id}] Erro ao carregar memória do Redis: {e_redis_load}", exc_info=True)
    return conversation_sessions[sender_id]

def reset_session_scheduling(session: Dict[str, Any]):
    """ Reseta os campos relacionados ao agendamento na sessão. """
    sender_name = session.get('patient_data', {}).get('name', '???')
    logger.debug(f"Resetando estado de agendamento da sessão {sender_name}")
    session["scheduling_status"] = None
    session["patient_data"] = {}
    session["suggested_slots"] = []
    session["chosen_slot"] = None
    session["event_to_modify"] = None
    session["multiple_events_found"] = []
    return session

def format_datetime_ptbr(dt_object: datetime.datetime) -> str:
    """ Formata um datetime para string legível em PT-BR usando locale. """
    if not isinstance(dt_object, datetime.datetime):
         return str(dt_object)
    try:
        return dt_object.strftime("%A, %d de %B às %H:%M")
    except Exception as e:
        logger.warning(f"Erro ao formatar data com locale pt-BR: {e}. Usando fallback.")
        return dt_object.strftime("%d/%m/%Y (%a) as %H:%M")

def validate_phone(phone_str: str) -> Optional[str]:
    """ Validação básica de telefone (procura por >= 10 dígitos). """
    digits = re.sub(r'\D', '', phone_str)
    if len(digits) >= 10:
        logger.debug(f"Telefone validado (básico): {digits}")
        return digits
    logger.debug(f"Telefone inválido (básico): {phone_str}")
    return None

def validate_email(email_str: str) -> bool:
    """ Validação básica de email (procura por @ e .). """
    is_valid = "@" in email_str and "." in email_str.split('@')[-1]
    logger.debug(f"Email {'válido' if is_valid else 'inválido'} (básico): {email_str}")
    return is_valid

def normalizar_horario_para_dateparser(texto: str) -> str:
    """Função utilitária para normalizar o texto para o dateparser."""
    texto = texto.lower()
    texto = re.sub(r"\b(segunda|terça|quarta|quinta|sexta|sábado|domingo)(-feira)?\b", "", texto)
    texto = re.sub(r"[,\.]", "", texto)
    texto = texto.replace("às", "as").replace("ás", "as").replace("á", "a")
    texto = re.sub(r"(\d)h(?!:)", r"\1:00", texto)
    texto = re.sub(r"\b(dia|no dia)\s+", "", texto)
    return texto.strip()

# --- Helper para Buscar e Apresentar Slots (Evita Duplicação) ---
def find_and_present_slots(session: Dict[str, Any], sender_id: str) -> Tuple[str, bool]:
    """
    Busca slots no CalDAV, atualiza a sessão e retorna a mensagem formatada
    ou de erro, e um booleano indicando se OpenAI é necessária (sempre False aqui).
    """
    logger.info(f"[{sender_id}] Executando find_and_present_slots...")
    try:
        tz = pytz.timezone(default_timezone)
        now = datetime.datetime.now(tz)
        rules = knowledge_handler.get_scheduling_rules()
        logger.debug(f"[{sender_id}] Usando regras de agendamento: {rules}")
        found_slots = caldav_handler.find_available_slots(
            start_search_dt=now,
            num_slots_to_find=5,
            consultation_duration_minutes=rules.get('duration_minutes', 45),
            block_duration_minutes=60,
            preferred_days=rules.get('preferred_days', [0, 1]),
            valid_start_hours=list(range(rules.get('start_hour', 14), rules.get('end_hour', 18)))
        )
        found_slots = found_slots[:5]
        logger.debug(f"[{sender_id}] Horários sugeridos pela busca: {[slot.isoformat() for slot in found_slots]}")

        # Salva os slots encontrados na sessão ANTES de formatar a mensagem
        session["suggested_slots"] = found_slots

        if found_slots:
            logger.info(f"[{sender_id}] {len(found_slots)} slots encontrados e salvos na sessão.")
            slot_options_list = []
            for i, slot_dt in enumerate(found_slots):
                formatted_dt = format_datetime_ptbr(slot_dt)
                slot_options_list.append({"index": i + 1, "datetime": slot_dt.isoformat(), "formatted": formatted_dt})

            # Formata a mensagem de apresentação
            response_parts = ["Perfeito! Com base nas informações, aqui estão os próximos horários disponíveis:"]
            for slot_info in slot_options_list:
                response_parts.append(f"{slot_info['index']}. {slot_info['formatted']}")
            response_parts.append("\nQual horário você prefere? (Responda com o número ou descreva o horário)")
            margot_response_final = "\n".join(response_parts)

            # Define o próximo estado como 'awaiting_choice'
            session["scheduling_status"] = "awaiting_choice"
            logger.debug(f"[{sender_id}] Estado definido para 'awaiting_choice'.")
            return margot_response_final, False # Retorna msg e indica que OpenAI não é necessária
        else:
            logger.warning(f"[{sender_id}] Nenhum slot encontrado no período buscado com as regras atuais.")
            margot_response_final = "Peço desculpas, mas não encontrei horários disponíveis que se encaixem nas regras de agendamento atuais (normalmente tardes de segunda e terça). A agenda pode estar completa ou houve um problema na verificação. A equipe da clínica foi notificada e entrará em contato para auxiliar."
            # Reseta a sessão pois não há como prosseguir
            reset_session_scheduling(session)
            logger.debug(f"[{sender_id}] Sessão resetada pois nenhum slot foi encontrado.")
            return margot_response_final, False
    except Exception as e_find:
        logger.error(f"[{sender_id}] Erro CRÍTICO ao buscar/apresentar slots no CalDAV: {e_find}", exc_info=True)
        margot_response_final = "Desculpe, tive um problema técnico grave ao verificar a agenda no momento. A equipe foi notificada. Por favor, tente novamente mais tarde ou aguarde nosso contato."
        # Reseta a sessão em caso de erro grave na busca
        reset_session_scheduling(session)
        logger.debug(f"[{sender_id}] Sessão resetada devido a erro na busca de slots.")
        return margot_response_final, False

# --- Endpoint Principal /whatsapp ---
@app.post("/whatsapp", tags=["Twilio"], summary="Recebe e responde mensagens do WhatsApp com fluxo de agendamento completo")
async def whatsapp_webhook(request: Request, From: str = Form(...), Body: str = Form(...)):
    sender_id = From
    user_message = Body.strip()
    logger.info(f"Msg Recebida | De: {sender_id} | Estado Atual: {conversation_sessions.get(sender_id, {}).get('scheduling_status')} | Mensagem: '{user_message}'")

    if not user_message:
         logger.warning(f"Mensagem vazia recebida de {sender_id}. Ignorando.")
         return Response(content=str(MessagingResponse()), media_type="application/xml")

    session = get_session(sender_id)
    session_history = session["history"]
    current_status = session["scheduling_status"]
    patient_data = session["patient_data"]
    margot_response_final = "Desculpe, ocorreu um erro inesperado. Por favor, tente novamente mais tarde."
    openai_call_needed = True # Default: precisa chamar OpenAI
    expected_data_for_openai = None

    try:
        # ======================================================================
        # --- FLUXO PRINCIPAL BASEADO NO ESTADO (scheduling_status) ---
        # ======================================================================

        # --- 1. ESTADO NULO: Conversa Geral ou Início de Fluxo ---
        if current_status is None:
            logger.debug(f"[{sender_id}] Estado: None. Analisando intenção ou RAG.")
            intent_schedule = re.search(r"\b(agendar|marcar|agendamento|marcando)\b.*\b(consulta|hor[aá]rio)\b", user_message, re.IGNORECASE)
            intent_reschedule = re.search(r"\b(remarcar|reagendar|mudar|alterar)\b.*\b(consulta|hor[aá]rio|agendamento)\b", user_message, re.IGNORECASE)
            intent_cancel = re.search(r"\b(cancelar|desmarcar)\b.*\b(consulta|hor[aá]rio|agendamento)\b", user_message, re.IGNORECASE)

            if intent_schedule:
                logger.info(f"[{sender_id}] Intenção de AGENDAR detectada.")
                session["scheduling_status"] = "awaiting_name"
                margot_response_final = "Claro! Será um prazer ajudar com o agendamento. Para começar, pode me informar o seu nome completo, por favor?"
                openai_call_needed = False
            elif intent_reschedule:
                 logger.info(f"[{sender_id}] Intenção de REMARCAR detectada.")
                 session["scheduling_status"] = "rebooking_awaiting_name"
                 margot_response_final = "Entendido. Para remarcar sua consulta, por favor, me informe o nome completo utilizado no agendamento original."
                 openai_call_needed = False
            elif intent_cancel:
                 logger.info(f"[{sender_id}] Intenção de CANCELAR detectada.")
                 session["scheduling_status"] = "cancelling_awaiting_name"
                 margot_response_final = "Compreendo. Para prosseguir com o cancelamento, por favor, me informe o nome completo utilizado no agendamento."
                 openai_call_needed = False
            else:
                logger.debug(f"[{sender_id}] Nenhuma intenção de agendamento explícita. Buscando RAG...")
                relevant_knowledge = knowledge_handler.find_relevant_info(query=user_message, conversation_history=session_history)
                if relevant_knowledge: logger.info(f"[{sender_id}] Conhecimento relevante (RAG) encontrado.")
                else: logger.info(f"[{sender_id}] Nenhum conhecimento relevante (RAG) encontrado.")

                margot_response_final = openai_handler.get_chat_response(
                    user_message=user_message, conversation_history=session_history,
                    relevant_knowledge=relevant_knowledge, current_schedule_state=current_status
                )
                openai_call_needed = False

        # --- 2. COLETA DE DADOS SEQUENCIAL (AGENDAMENTO) ---
        elif current_status == "awaiting_name":
            logger.debug(f"[{sender_id}] Estado: awaiting_name.")
            if len(user_message) > 3:
                patient_data["name"] = user_message.title()
                session["scheduling_status"] = "awaiting_phone"
                margot_response_final = f"Obrigada, {patient_data['name']}! Agora, por favor, me informe o seu número de telefone com DDD para contato."
                openai_call_needed = False
            else:
                expected_data_for_openai = "name"
                openai_call_needed = True

        elif current_status == "awaiting_phone":
            logger.debug(f"[{sender_id}] Estado: awaiting_phone.")
            validated_phone = validate_phone(user_message)
            if validated_phone:
                patient_data["phone"] = validated_phone
                session["scheduling_status"] = "awaiting_email"
                margot_response_final = "Perfeito. E qual o seu melhor endereço de e-mail?"
                openai_call_needed = False
            else:
                expected_data_for_openai = "phone"
                openai_call_needed = True

        elif current_status == "awaiting_email":
            logger.debug(f"[{sender_id}] Estado: awaiting_email.")
            if validate_email(user_message):
                patient_data["email"] = user_message.lower()
                session["scheduling_status"] = "awaiting_procedure"
                margot_response_final = "Estamos quase lá! Você tem interesse em algum procedimento específico ou é uma consulta geral de avaliação?"
                openai_call_needed = False
            else:
                expected_data_for_openai = "email"
                openai_call_needed = True

        elif current_status == "awaiting_procedure":
            logger.debug(f"[{sender_id}] Estado: awaiting_procedure.")
            if user_message:
                patient_data["procedure"] = user_message
                session["scheduling_status"] = "awaiting_indication"
                margot_response_final = "Entendido. Só mais uma pergunta: você foi indicado(a) por alguém? Se sim, por quem? (Se não, pode só dizer 'Não')."
                openai_call_needed = False
            else:
                expected_data_for_openai = "procedure"
                openai_call_needed = True

        elif current_status == "awaiting_indication":
            logger.debug(f"[{sender_id}] Estado: awaiting_indication.")
            patient_data["indication"] = user_message if user_message else "Não informado"
            logger.info(f"[{sender_id}] Coleta de dados concluída (Indicação: '{patient_data['indication']}'). Iniciando busca de horários diretamente...")

            # --- EXECUTA A BUSCA DE SLOTS DIRETAMENTE ---
            margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
            # O estado da sessão (scheduling_status) é atualizado dentro de find_and_present_slots

        # --- 4. RECEBER ESCOLHA DO HORÁRIO ---
        elif current_status == "awaiting_choice":
            logger.debug(f"[{sender_id}] Estado: awaiting_choice. Analisando resposta: '{user_message}'")
            suggested_slots = session.get("suggested_slots", [])
            matched_slot = None
            tz = pytz.timezone(default_timezone)

            if not suggested_slots:
                 logger.warning(f"[{sender_id}] Chegou em awaiting_choice sem suggested_slots na sessão! Tentando buscar novamente...")
                 margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
                 pass # Pula o resto da lógica deste estado
            else:
                logger.debug(f"[{sender_id}] Slots sugeridos na sessão para escolha: {[s.isoformat() for s in suggested_slots]}")
                # --- ESTRATÉGIA DE MATCHING EM CAMADAS ---
                # 1. Tentar Número Explícito
                match_num = re.search(r"^\s*(\d+)\s*$", user_message)
                if match_num:
                    try:
                        idx = int(match_num.group(1)) - 1
                        if 0 <= idx < len(suggested_slots):
                            matched_slot = suggested_slots[idx]
                            logger.info(f"[{sender_id}] Slot escolhido por NÚMERO direto: {idx+1}")
                    except (ValueError, IndexError):
                        logger.debug(f"[{sender_id}] Número '{match_num.group(1)}' inválido ou fora do range.")

                # 2. Tentar Correspondência de Palavras-Chave/Data Parcial
                if not matched_slot:
                    logger.debug(f"[{sender_id}] Tentando match por palavras-chave/data parcial...")
                    normalized_msg = user_message.lower()
                    normalized_msg = re.sub(r"[^\w\s\:]", "", normalized_msg)
                    normalized_msg = re.sub(r"(\d)\s*h(\s|$)", r"\1:00\2", normalized_msg)
                    normalized_msg = normalized_msg.replace(" as ", " ").replace(" às ", " ")
                    possible_numbers = [int(n) for n in re.findall(r'\b(\d+)\b', normalized_msg)]
                    possible_matches = []
                    for i, slot in enumerate(suggested_slots):
                        match_score = 0
                        slot_day = slot.day; slot_hour = slot.hour; slot_minute = slot.minute
                        if slot_day in possible_numbers: match_score += 2
                        if slot_hour in possible_numbers: match_score += 1
                        if slot_minute == 0:
                           if f"{slot_hour}:00" in normalized_msg and slot_hour in possible_numbers: match_score += 1
                        elif slot_minute != 0:
                             if f"{slot_hour}:{slot_minute}" in normalized_msg and slot_hour in possible_numbers: match_score += 1
                        if match_score > 0:
                           logger.debug(f"  - Slot {i+1} ({format_datetime_ptbr(slot)}): Score {match_score} (Msg: '{normalized_msg}', Números: {possible_numbers})")
                           possible_matches.append({"slot": slot, "score": match_score, "index": i})
                    if possible_matches:
                       possible_matches.sort(key=lambda x: x["score"], reverse=True)
                       best_match = possible_matches[0]
                       if best_match["score"] >= 2 and (len(possible_matches) == 1 or best_match["score"] >= possible_matches[1]["score"] + 2):
                            matched_slot = best_match["slot"]
                            logger.info(f"[{sender_id}] Slot escolhido por PALAVRAS-CHAVE: {best_match['index']+1} (Score: {best_match['score']})")
                       else:
                            logger.debug(f"[{sender_id}] Match por palavras-chave ambíguo ou score baixo. Melhores scores: {[p['score'] for p in possible_matches]}")

                # 3. Tentar dateparser (NÃO estrito) + Tolerância
                if not matched_slot:
                    logger.debug(f"[{sender_id}] Tentando match com dateparser (não-estrito)...")
                    try:
                        normalized_message_dp = normalizar_horario_para_dateparser(user_message)
                        parsed_dt = parse_date(normalized_message_dp, languages=['pt'], settings={
                            "PREFER_DATES_FROM": "future", "TIMEZONE": str(tz),
                            "RETURN_AS_TIMEZONE_AWARE": True, "RELATIVE_BASE": datetime.datetime.now(tz),
                            "STRICT_PARSING": False
                        })
                        if parsed_dt:
                            logger.debug(f"[{sender_id}] Dateparser (não-estrito) retornou: {parsed_dt.isoformat()}")
                            closest_slot = None; min_diff = float('inf')
                            for i, slot in enumerate(suggested_slots):
                                diff = abs((slot - parsed_dt).total_seconds())
                                if diff <= 900:
                                   logger.debug(f"  - Slot {i+1} vs Parsed: Diff {diff:.0f}s (Dentro da tolerância)")
                                   if diff < min_diff:
                                       min_diff = diff; closest_slot = {"slot": slot, "index": i, "diff": diff}
                                else: logger.debug(f"  - Slot {i+1} vs Parsed: Diff {diff:.0f}s (FORA da tolerância)")
                            if closest_slot:
                                matched_slot = closest_slot["slot"]
                                logger.info(f"[{sender_id}] Slot escolhido por DATEPARSER+Tolerância: {closest_slot['index']+1} (Diff: {closest_slot['diff']:.0f}s)")
                            else: logger.debug(f"[{sender_id}] Dateparser ok, mas nenhum slot na tolerância.")
                        else: logger.debug(f"[{sender_id}] Dateparser (não-estrito) não parseou: '{normalized_message_dp}'")
                    except Exception as e_dp: logger.warning(f"[{sender_id}] Erro no dateparser: {e_dp}", exc_info=False)

                # 4. Fallback: Tentar GPT (CORRIGIDO)
                if not matched_slot:
                    logger.debug(f"[{sender_id}] Tentando match com GPT (fallback)...")
                    try:
                        horario_lista_texto = "\n".join([f"{i+1}. {format_datetime_ptbr(slot)}" for i, slot in enumerate(suggested_slots)])
                        pergunta = (
                            f"O paciente recebeu a seguinte lista de horários:\n{horario_lista_texto}\n\n"
                            f"A resposta do paciente foi: \"{user_message}\"\n\n"
                            f"Qual NÚMERO da opção o paciente escolheu? Responda SÓ o número (1, 2, 3...) ou 0 se incerto."
                        )
                        # --- CHAMADA CORRIGIDA COM KEYWORD ARGUMENTS ---
                        resposta_gpt = openai_handler.get_chat_response(
                            user_message=pergunta,
                            conversation_history=[], # Histórico vazio para esta análise específica
                            patient_data=patient_data, # Passa o dicionário
                            current_schedule_state="awaiting_choice" # Passa o estado
                        ).strip()
                        # --- FIM DA CORREÇÃO ---
                        logger.debug(f"[{sender_id}] Resposta bruta do GPT para escolha: '{resposta_gpt}'")
                        match_gpt_num = re.search(r"\b(\d+)\b", resposta_gpt)
                        if match_gpt_num:
                             gpt_choice = int(match_gpt_num.group(1))
                             idx = gpt_choice - 1
                             if 1 <= gpt_choice <= len(suggested_slots):
                                  matched_slot = suggested_slots[idx]
                                  logger.info(f"[{sender_id}] Slot escolhido via análise GPT: {idx+1}")
                             elif gpt_choice == 0: logger.info(f"[{sender_id}] GPT indicou incerteza (0).")
                             else: logger.warning(f"[{sender_id}] GPT retornou número inválido/range: '{gpt_choice}'.")
                        else: logger.warning(f"[{sender_id}] Não extraiu número da resposta GPT: '{resposta_gpt}'")
                    except Exception as e_gpt:
                         logger.error(f"[{sender_id}] Erro ao chamar/processar fallback GPT: {e_gpt}", exc_info=True) # Loga erro completo

                # --- Processamento do Resultado do Matching ---
                if matched_slot:
                    session["chosen_slot"] = matched_slot
                    formatted_chosen = format_datetime_ptbr(matched_slot)
                    margot_response_final = f"Perfeito! Podemos confirmar sua consulta para {formatted_chosen}?"
                    session["scheduling_status"] = "awaiting_confirmation"
                    openai_call_needed = False
                    logger.info(f"[{sender_id}] Match bem sucedido. Indo para confirmação do slot: {matched_slot.isoformat()}")
                else:
                    # Se NENHUMA estratégia funcionou
                    logger.warning(f"[{sender_id}] Falha em todas as estratégias de matching para: '{user_message}'. Pedindo para reformular.")
                    response_parts = ["Desculpe, não consegui identificar qual horário você escolheu."]
                    response_parts.append("\nLembrando as opções:")
                    for i, slot_dt in enumerate(suggested_slots):
                        response_parts.append(f"{i+1}. {format_datetime_ptbr(slot_dt)}")
                    response_parts.append("\nPor favor, tente responder apenas com o *número da opção* ou reescreva a data/hora (ex: 'Terça, dia 5, às 16:00').")
                    margot_response_final = "\n".join(response_parts)
                    openai_call_needed = False

        # --- 5. AGUARDANDO CONFIRMAÇÃO FINAL ---
        elif current_status == "awaiting_confirmation":
            logger.debug(f"[{sender_id}] Estado: awaiting_confirmation.")
            chosen_dt = session.get("chosen_slot")
            if not chosen_dt or not isinstance(chosen_dt, datetime.datetime):
                logger.error(f"[{sender_id}] Erro crítico: 'awaiting_confirmation' sem 'chosen_slot'!")
                margot_response_final = "Desculpe, erro interno. Não lembro o horário. Vamos buscar de novo?"
                margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
            else:
                confirmation_positive = re.search(r"\b(sim|s|ok|positivo|confirmo|confirmado|pode ser|pode)\b", user_message, re.IGNORECASE)
                confirmation_negative = re.search(r"\b(n[aã]o|cancela|errado|mudar|outro|nao)\b", user_message, re.IGNORECASE)

                if confirmation_positive:
                    logger.info(f"[{sender_id}] Confirmação recebida para {format_datetime_ptbr(chosen_dt)}. Agendando...")
                    rules = knowledge_handler.get_scheduling_rules()
                    duration_minutes = rules.get('duration_minutes', 45)
                    end_time_dt = chosen_dt + datetime.timedelta(minutes=duration_minutes)
                    try:
                        # A chamada abaixo assume que caldav_handler.py foi corrigido para aceitar 'patient_email'
                        # Início do bloco try para salvar dados e confirmar agendamento
                        success, message = caldav_handler.book_appointment(
                            start_time=chosen_dt, end_time=end_time_dt,
                            patient_name=patient_data.get("name"), patient_contact=patient_data.get("phone"),
                            patient_email=patient_data.get("email"), indication_source=patient_data.get("indication"),
                            procedure_interest=patient_data.get("procedure")
                        )
                        if success:
                            try:
                                import redis
                                redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
                                redis_key = f"memoria:{sender_id}"
                                redis_data = {
                                    "name": patient_data.get("name"),
                                    "phone": patient_data.get("phone"),
                                    "email": patient_data.get("email"),
                                    "procedure": patient_data.get("procedure"),
                                    "indication": patient_data.get("indication"),
                                    "last_datetime": chosen_dt.isoformat()
                                }
                                redis_client.hset(redis_key, mapping=redis_data)
                                logger.info(f"[{sender_id}] Dados do paciente salvos na memória Redis.")
                            except Exception as e_redis:
                                logger.error(f"[{sender_id}] Erro ao salvar dados no Redis: {e_redis}", exc_info=True)
                            logger.info(f"[{sender_id}] Agendamento OK no CalDAV.")
                            margot_response_final = (
                                f"✅ *Agendamento Confirmado!* 🎉\n\n"
                                f"{patient_data.get('name', 'Sua consulta')} foi agendada com sucesso para:\n"
                                f"*{format_datetime_ptbr(chosen_dt)}*\n\n"
                                f"Procedimento/Interesse: {patient_data.get('procedure', 'Avaliação Geral')}\n"
                                f"Com o Dr. Juarez Missel.\n\n"
                                f"📍 *Endereço:* {knowledge_handler._format_address() or 'Rua Coronel Gabriel Bastos, 371 – Passo Fundo/RS'}\n"
                                f"⏰ *Lembrete:* Chegue com 15 minutos de antecedência.\n"
                                f"📋 Se estiver usando medicamentos, leve a lista.\n"
                                f"🧾 Se tiver exames relacionados, leve-os também.\n\n"
                                f"Qualquer dúvida, estou à disposição. A {clinic_name} agradece a confiança!"
                            )
                            # Envio da confirmação para a equipe de relacionamento
                            relationship_number = os.getenv("RELATIONSHIP_TEAM_WHATSAPP")
                            if relationship_number:
                                try:
                                    team_msg = (
                                        f"[MARGOT] Nova consulta agendada!\n\n"
                                        f"📌 Nome: {patient_data.get('name', 'N/A')}\n"
                                        f"📞 Telefone: {patient_data.get('phone', 'N/A')}\n"
                                        f"📧 Email: {patient_data.get('email', 'N/A')}\n"
                                        f"🗓 Data/Hora: {format_datetime_ptbr(chosen_dt)}\n"
                                        f"💬 Procedimento: {patient_data.get('procedure', 'Não informado')}\n"
                                        f"🔁 Indicação: {patient_data.get('indication', 'Não informado')}"
                                    )
                                    twilio_client.messages.create(
                                        from_=twilio_whatsapp_number,
                                        to=relationship_number,
                                        body=team_msg
                                    )
                                    logger.info(f"[{sender_id}] Mensagem enviada para a equipe de relacionamento.")
                                except Exception as e_notify:
                                    logger.error(f"[{sender_id}] Erro ao notificar equipe de relacionamento: {e_notify}", exc_info=True)
                            session = reset_session_scheduling(session)
                            openai_call_needed = False
                        else:
                            logger.warning(f"[{sender_id}] Falha ao agendar no CalDAV: {message}")
                            margot_response_final = f"{message or 'Desculpe,'} parece que este horário foi ocupado. Gostaria de tentar outro?"
                            session["scheduling_status"] = "awaiting_choice" # Volta para escolha
                            session["chosen_slot"] = None
                            # Tenta reapresentar slots restantes
                            if session.get("suggested_slots"):
                                failed_slot_iso = chosen_dt.isoformat()
                                session["suggested_slots"] = [s for s in session["suggested_slots"] if s.isoformat() != failed_slot_iso]
                                if session["suggested_slots"]:
                                    response_parts = [margot_response_final, "\nRelembrando os horários que sobraram:"]
                                    for i, slot_dt in enumerate(session["suggested_slots"]): response_parts.append(f"{i+1}. {format_datetime_ptbr(slot_dt)}")
                                    response_parts.append("\nQual você prefere?")
                                    margot_response_final = "\n".join(response_parts)
                                    openai_call_needed = False
                                else: # Se não sobraram, busca novamente
                                     margot_response_final += " Não encontrei mais opções naquela busca. Vou verificar novamente..."
                                     margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
                            else: # Se não tinha lista, busca novamente
                                margot_response_final += " Vou verificar novamente a disponibilidade..."
                                margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
                    except Exception as e_book:
                        logger.error(f"[{sender_id}] Erro CRÍTICO em book_appointment: {e_book}", exc_info=True)
                        margot_response_final = "Desculpe, erro técnico grave ao confirmar. Equipe notificada."
                        session = reset_session_scheduling(session)
                        openai_call_needed = False
                elif confirmation_negative:
                    logger.info(f"[{sender_id}] Usuário NÃO confirmou. Voltando para escolha.")
                    session["scheduling_status"] = "awaiting_choice"
                    session["chosen_slot"] = None
                    if session.get("suggested_slots"):
                        response_parts = ["Entendido. Vamos escolher outro.", "\nOpções:"]
                        for i, slot_dt in enumerate(session["suggested_slots"]): response_parts.append(f"{i+1}. {format_datetime_ptbr(slot_dt)}")
                        response_parts.append("\nQual prefere?")
                        margot_response_final = "\n".join(response_parts)
                    else:
                        margot_response_final = "Entendido. Vou verificar novamente a disponibilidade..."
                        margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
                    openai_call_needed = False
                else:
                    logger.warning(f"[{sender_id}] Resposta de confirmação ambígua: '{user_message}'.")
                    expected_data_for_openai = "confirmation"
                    session["_chosen_slot_formatted_for_openai"] = format_datetime_ptbr(chosen_dt)
                    openai_call_needed = True

        # --- 6. FLUXO DE CANCELAMENTO (Sem alterações na lógica principal) ---
        elif current_status == "cancelling_awaiting_name":
             logger.debug(f"[{sender_id}] Estado: cancelling_awaiting_name.")
             if len(user_message) > 3:
                 patient_data["name_for_cancel"] = user_message.title()
                 session["scheduling_status"] = "cancelling_finding"
                 margot_response_final = f"Obrigada, {patient_data['name_for_cancel']}. Buscando agendamentos..."
                 openai_call_needed = False
             else: expected_data_for_openai = "name"; openai_call_needed = True
        elif current_status == "cancelling_finding":
             logger.info(f"[{sender_id}] Estado: cancelling_finding. Buscando para {patient_data.get('name_for_cancel')}")
             try:
                 tz = pytz.timezone(default_timezone); now = datetime.datetime.now(tz); search_end = now + relativedelta(months=6)
                 found_events = caldav_handler.find_appointments_by_details(
                     patient_name=patient_data.get('name_for_cancel'), start_range=now, end_range=search_end
                 )
                 if not found_events:
                      margot_response_final = f"Não encontrei agendamentos futuros para {patient_data.get('name_for_cancel', 'você')}. Nome correto?"
                      session = reset_session_scheduling(session)
                 elif len(found_events) == 1:
                      session["event_to_modify"] = found_events[0]; session["scheduling_status"] = "cancelling_awaiting_confirmation"
                      margot_response_final = f"Encontrei: *{found_events[0]['summary']}* para *{format_datetime_ptbr(found_events[0]['start'])}*. Cancelar este? (Sim/Não)"
                 else:
                      session["multiple_events_found"] = found_events; session["scheduling_status"] = "cancelling_awaiting_choice"
                      response_parts = [f"Encontrei {len(found_events)} agendamentos para {patient_data.get('name_for_cancel', 'você')}:"]
                      for i, event in enumerate(found_events): response_parts.append(f"{i+1}. {format_datetime_ptbr(event['start'])} ({event['summary']})")
                      response_parts.append("\nQual deles (número) deseja cancelar?")
                      margot_response_final = "\n".join(response_parts)
                 openai_call_needed = False
             except Exception as e: logger.error(f"Erro busca cancel: {e}", exc_info=True); margot_response_final = "Erro ao buscar agends."; session = reset_session_scheduling(session); openai_call_needed = False
        elif current_status == "cancelling_awaiting_choice":
             chosen_index = -1
             try:
                 match = re.search(r"^\s*(\d+)\s*$", user_message);
                 if match: chosen_index = int(match.group(1))
             except: pass
             if 1 <= chosen_index <= len(session.get("multiple_events_found", [])):
                 session["event_to_modify"] = session["multiple_events_found"][chosen_index - 1]; session["multiple_events_found"] = []
                 session["scheduling_status"] = "cancelling_awaiting_confirmation"
                 margot_response_final = f"Selecionado: *{session['event_to_modify']['summary']}* para *{format_datetime_ptbr(session['event_to_modify']['start'])}*. Confirmar cancelamento? (Sim/Não)"
                 openai_call_needed = False
             else: expected_data_for_openai = "multiple_choice"; session["_cancel_rebook_context_for_openai"] = {"action": "cancel", "events": session.get("multiple_events_found")}; openai_call_needed = True
        elif current_status == "cancelling_awaiting_confirmation":
             confirmation_positive = re.search(r"\b(sim|s|ok|positivo|confirmo|confirmado|pode cancelar)\b", user_message, re.IGNORECASE)
             confirmation_negative = re.search(r"\b(n[aã]o|espera|mudei de ideia|deixa|nao)\b", user_message, re.IGNORECASE)
             event_to_cancel = session.get("event_to_modify")
             if not event_to_cancel or not event_to_cancel.get('id'): logger.error(f"Crit Err: cancel confirm sem event!"); margot_response_final = "Erro interno. Recomeçar cancelamento?"; session = reset_session_scheduling(session); session["scheduling_status"] = "cancelling_awaiting_name"; openai_call_needed = False
             elif confirmation_positive:
                 try:
                      success, message = caldav_handler.cancel_appointment(event_identifier=event_to_cancel['id'])
                      if success: margot_response_final = f"✅ Cancelamento Confirmado! Agendamento de {format_datetime_ptbr(event_to_cancel['start'])} cancelado. {message or ''}"
                      else: margot_response_final = f"Problema ao cancelar: {message} Contate a clínica."
                      session = reset_session_scheduling(session); openai_call_needed = False
                 except Exception as e: logger.error(f"Erro CRIT cancel: {e}", exc_info=True); margot_response_final = "Erro técnico grave."; session = reset_session_scheduling(session); openai_call_needed = False
             elif confirmation_negative: margot_response_final = "Ok, não cancelado. Algo mais?"; session = reset_session_scheduling(session); openai_call_needed = False
             else: expected_data_for_openai = "cancel_confirmation"; session["_cancel_rebook_context_for_openai"] = {"action": "cancel", "event_details": event_to_cancel}; openai_call_needed = True

        # --- 7. FLUXO DE REAGENDAMENTO ---
        elif current_status == "rebooking_awaiting_name":
             if len(user_message) > 3: patient_data["name_for_rebook"] = user_message.title(); session["scheduling_status"] = "rebooking_finding"; margot_response_final = f"Obrigada, {patient_data['name_for_rebook']}. Buscando agendamentos para remarcar..."; openai_call_needed = False
             else: expected_data_for_openai = "name"; openai_call_needed = True
        elif current_status == "rebooking_finding":
             try:
                 tz = pytz.timezone(default_timezone); now = datetime.datetime.now(tz); search_end = now + relativedelta(months=6)
                 found_events = caldav_handler.find_appointments_by_details(patient_name=patient_data.get('name_for_rebook'), start_range=now, end_range=search_end)
                 if not found_events:
                      margot_response_final = f"Não encontrei agendamentos futuros para {patient_data.get('name_for_rebook', 'você')}. Fazer novo agendamento?"
                      session = reset_session_scheduling(session); session["patient_data"]["name"] = patient_data.get("name_for_rebook")
                      session["scheduling_status"] = "awaiting_phone"; margot_response_final = f"Não achei agendamentos para {patient_data.get('name_for_rebook')}. Vamos fazer um novo. Qual seu telefone com DDD?"
                 elif len(found_events) == 1:
                      session["event_to_modify"] = found_events[0]; session["scheduling_status"] = "rebooking_awaiting_confirmation"
                      margot_response_final = f"Encontrei: *{found_events[0]['summary']}* para *{format_datetime_ptbr(found_events[0]['start'])}*. Remarcar este? (Sim/Não)"
                 else:
                      session["multiple_events_found"] = found_events; session["scheduling_status"] = "rebooking_awaiting_choice"
                      response_parts = [f"Encontrei {len(found_events)} agendamentos para {patient_data.get('name_for_rebook', 'você')}:"]
                      for i, event in enumerate(found_events): response_parts.append(f"{i+1}. {format_datetime_ptbr(event['start'])} ({event['summary']})")
                      response_parts.append("\nQual deles (número) deseja remarcar?")
                      margot_response_final = "\n".join(response_parts)
                 openai_call_needed = False
             except Exception as e: logger.error(f"Erro busca rebook: {e}", exc_info=True); margot_response_final = "Erro ao buscar agends."; session = reset_session_scheduling(session); openai_call_needed = False
        elif current_status == "rebooking_awaiting_choice":
             chosen_index = -1
             try:
                 match = re.search(r"^\s*(\d+)\s*$", user_message);
                 if match: chosen_index = int(match.group(1))
             except: pass
             if 1 <= chosen_index <= len(session.get("multiple_events_found", [])):
                 session["event_to_modify"] = session["multiple_events_found"][chosen_index - 1]; session["multiple_events_found"] = []
                 session["scheduling_status"] = "rebooking_awaiting_confirmation"
                 margot_response_final = f"Ok: *{session['event_to_modify']['summary']}* em *{format_datetime_ptbr(session['event_to_modify']['start'])}*. Confirmar remarcação? (Sim/Não)"
                 openai_call_needed = False
             else: expected_data_for_openai = "multiple_choice"; session["_cancel_rebook_context_for_openai"] = {"action": "rebook", "events": session.get("multiple_events_found")}; openai_call_needed = True
        elif current_status == "rebooking_awaiting_confirmation":
             confirmation_positive = re.search(r"\b(sim|s|ok|positivo|confirmo|confirmado|quero remarcar)\b", user_message, re.IGNORECASE)
             confirmation_negative = re.search(r"\b(n[aã]o|espera|mudei de ideia|deixa|nao)\b", user_message, re.IGNORECASE)
             event_to_rebook = session.get("event_to_modify")
             if not event_to_rebook or not event_to_rebook.get('id'): logger.error(f"Crit Err: rebook confirm sem event!"); margot_response_final = "Erro interno. Recomeçar remarcação?"; session = reset_session_scheduling(session); session["scheduling_status"] = "rebooking_awaiting_name"; openai_call_needed = False
             elif confirmation_positive:
                 try:
                      success, message = caldav_handler.cancel_appointment(event_identifier=event_to_rebook['id'])
                      if success:
                           logger.info(f"[{sender_id}] Evento antigo cancelado. Coletando/verificando dados para novo.")
                           summary_parts = event_to_rebook.get("summary", "").split(" - "); extracted_name = summary_parts[-1].strip() if len(summary_parts) > 1 else patient_data.get("name_for_rebook")
                           session["patient_data"]["name"] = extracted_name.title() if extracted_name else "Nome não encontrado"
                           session["event_to_modify"] = None; session["multiple_events_found"] = []; session["suggested_slots"] = []; session["chosen_slot"] = None
                           # Verifica se precisamos de dados adicionais ANTES de buscar slots
                           if not session["patient_data"].get("phone"):
                               session["scheduling_status"] = "rebooking_awaiting_phone"; margot_response_final = f"Ok! Cancelei o anterior. Para o novo, confirme seu telefone com DDD."; openai_call_needed = False
                           elif not session["patient_data"].get("email"):
                               session["scheduling_status"] = "rebooking_awaiting_email"; margot_response_final = f"Ok! Cancelei o anterior. E seu e-mail para o novo?"; openai_call_needed = False
                           else: # --- EXECUTA A BUSCA DE SLOTS DIRETAMENTE ---
                                logger.info(f"[{sender_id}] Dados completos para remarcação. Buscando novos slots...")
                                margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
                      else:
                           logger.warning(f"Falha ao cancelar antigo p/ remarcar: {message}")
                           margot_response_final = f"Problema ao cancelar anterior: {message} Tente de novo ou contate a clínica."; session["scheduling_status"] = "rebooking_awaiting_confirmation"; openai_call_needed = False
                 except Exception as e: logger.error(f"Erro CRIT rebook cancel: {e}", exc_info=True); margot_response_final = "Erro técnico grave."; session = reset_session_scheduling(session); openai_call_needed = False
             elif confirmation_negative: margot_response_final = "Ok, agendamento mantido. Algo mais?"; session = reset_session_scheduling(session); openai_call_needed = False
             else: expected_data_for_openai = "rebook_confirmation"; session["_cancel_rebook_context_for_openai"] = {"action": "rebook", "event_details": event_to_rebook}; openai_call_needed = True

        # --- 8. ESTADOS DE COLETA PÓS-REMARCAÇÃO ---
        elif current_status == "rebooking_awaiting_phone":
             validated_phone = validate_phone(user_message)
             if validated_phone:
                 patient_data["phone"] = validated_phone
                 if not patient_data.get("email"):
                     session["scheduling_status"] = "rebooking_awaiting_email"; margot_response_final = "Obrigada. E qual seu e-mail?"; openai_call_needed = False
                 else: # --- EXECUTA A BUSCA DE SLOTS DIRETAMENTE ---
                      logger.info(f"[{sender_id}] Dados pós-rebook (tel ok). Buscando slots...")
                      margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
             else: expected_data_for_openai = "phone"; openai_call_needed = True
        elif current_status == "rebooking_awaiting_email":
             if validate_email(user_message):
                 patient_data["email"] = user_message.lower()
                 # --- EXECUTA A BUSCA DE SLOTS DIRETAMENTE ---
                 logger.info(f"[{sender_id}] Dados pós-rebook (email ok). Buscando slots...")
                 margot_response_final, openai_call_needed = find_and_present_slots(session, sender_id)
             else: expected_data_for_openai = "email"; openai_call_needed = True

        # --- CASO DEFAULT / ESTADO DESCONHECIDO ---
        else:
            logger.error(f"[{sender_id}] Estado desconhecido: {current_status}. Resetando.")
            margot_response_final = "Desculpe, nos perdemos. Recomeçar? Em que posso ajudar?"
            session = reset_session_scheduling(session); openai_call_needed = False

        # ======================================================================
        # --- FIM DO FLUXO BASEADO NO ESTADO ---
        # ======================================================================

        # --- Chamada à OpenAI (se necessária) ---
        if openai_call_needed:
            logger.debug(f"[{sender_id}] Chamando OpenAI para estado '{session.get('scheduling_status')}' com expected_data='{expected_data_for_openai}'")
            openai_contexts = {
                "current_schedule_state": session.get('scheduling_status'), "patient_data": session.get('patient_data'),
                "expected_data": expected_data_for_openai,
                "chosen_slot_context": session.pop("_chosen_slot_formatted_for_openai", None),
                "cancel_rebook_context": session.pop("_cancel_rebook_context_for_openai", None),
            }
            openai_contexts = {k: v for k, v in openai_contexts.items() if v is not None}
            margot_response_final = openai_handler.get_chat_response(
                user_message=user_message, conversation_history=session_history, **openai_contexts
            )

    except Exception as e_main_flow:
        logger.error(f"[{sender_id}] Erro GRAVE INESPERADO (Estado: {current_status}): {e_main_flow}", exc_info=True)
        margot_response_final = "Desculpe, erro interno inesperado. Equipe notificada. Tente mais tarde."
        try: session = reset_session_scheduling(session); logger.info(f"[{sender_id}] Sessão resetada pós-erro grave.")
        except Exception as e_reset: logger.error(f"Erro ao resetar sessão: {e_reset}", exc_info=True)
        openai_call_needed = False

    # --- Atualização do Histórico e Envio da Resposta ---
    try:
        if user_message: session_history.append({"role": "user", "content": user_message})
        cleaned_response = margot_response_final.strip() if margot_response_final else ""
        if cleaned_response:
            session_history.append({"role": "assistant", "content": cleaned_response})
            logger.info(f"Resposta Margot | Para: {sender_id} | Estado Final: {session.get('scheduling_status')} | Resposta: '{cleaned_response[:100]}...'")
        else:
             logger.warning(f"[{sender_id}] Resposta final vazia. Nenhuma resposta enviada.")
             return Response(content=str(MessagingResponse()), media_type="application/xml")

        if len(session_history) > MAX_HISTORY_LENGTH * 2: session["history"] = session_history[-(MAX_HISTORY_LENGTH * 2):]
        else: session["history"] = session_history
        conversation_sessions[sender_id] = session

        MAX_MSG_LENGTH = 1550
        response_to_send = cleaned_response
        final_twiml_response = MessagingResponse(); messages_sent_count = 0
        while len(response_to_send.encode('utf-8')) > MAX_MSG_LENGTH:
            split_point = -1; best_newline = response_to_send.rfind('\n', 0, MAX_MSG_LENGTH)
            if best_newline != -1 and best_newline > MAX_MSG_LENGTH // 2: split_point = best_newline
            else:
                best_space = response_to_send.rfind(' ', 0, MAX_MSG_LENGTH)
                if best_space != -1 and best_space > MAX_MSG_LENGTH // 2: split_point = best_space
                else: split_point = MAX_MSG_LENGTH
            message_part = response_to_send[:split_point].strip()
            response_to_send = response_to_send[split_point:].strip()
            if message_part:
                 logger.debug(f"Enviando parte {messages_sent_count + 1} API ({len(message_part)} chars) para {sender_id}")
                 try:
                      twilio_client.messages.create(from_=twilio_whatsapp_number, body=message_part, to=sender_id)
                      messages_sent_count += 1; time.sleep(0.8)
                 except Exception as e_send_part:
                      logger.error(f"Erro envio API: {e_send_part}", exc_info=True)
                      error_twiml = MessagingResponse(); error_twiml.message("Erro ao enviar parte da resposta. Tente novamente?")
                      return Response(content=str(error_twiml), media_type="application/xml")
        if response_to_send:
             logger.debug(f"Enviando parte final TwiML ({len(response_to_send)} chars) para {sender_id}.")
             final_twiml_response.message(response_to_send); messages_sent_count += 1
        elif messages_sent_count > 0:
             logger.info(f"[{sender_id}] Nenhuma parte final TwiML. Retornando TwiML vazio.")
             return Response(content=str(MessagingResponse()), media_type="application/xml")

        return Response(content=str(final_twiml_response), media_type="application/xml")

    except Exception as e_send_final:
        logger.error(f"[{sender_id}] Erro CRÍTICO envio/histórico: {e_send_final}", exc_info=True)
        error_twiml = MessagingResponse(); error_twiml.message("Erro final. Equipe notificada.")
        return Response(content=str(error_twiml), media_type="application/xml")

# --- Rota de Status ---
@app.get("/", tags=["Status"], summary="Verifica o status da API")
async def root():
    logger.info("Rota raiz '/' acessada.")
    return {"message": f"API da {margot_persona_name} ({clinic_name}) está online!", "version": app.version}

# --- Execução Local ---
if __name__ == "__main__":
    import uvicorn
    host_address = os.getenv("HOST", "0.0.0.0")
    port_number = int(os.getenv("PORT", "8000"))
    reload_flag = os.getenv("DEBUG", "false").lower() == "true"

    log_config = uvicorn.config.LOGGING_CONFIG
    if reload_flag:
        log_config["formatters"]["default"]["fmt"] = log_format
        log_config["formatters"]["access"]["fmt"] = '%(asctime)s - %(levelname)s - %(client_addr)s - "%(request_line)s" %(status_code)s'

    logger.info(f"Iniciando servidor Uvicorn em http://{host_address}:{port_number} (Reload: {reload_flag})")
    uvicorn.run(
        "main:app", host=host_address, port=port_number, reload=reload_flag,
        log_config=log_config if reload_flag else None,
        log_level=logging.getLevelName(logger.getEffectiveLevel()).lower()
    )