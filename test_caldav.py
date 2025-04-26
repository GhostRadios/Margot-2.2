# test_caldav.py - Script isolado para testar CaldavHandler
import os
import logging
import datetime
import pytz
import time
import uuid
from dotenv import load_dotenv
from dateutil.relativedelta import relativedelta

# Importa o handler que queremos testar
from caldav_handler import CaldavHandler

# Configuração básica de logging para ver o que está acontecendo
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format)
logging.getLogger("caldav").setLevel(logging.INFO) # Logs do CalDAV em INFO
logger = logging.getLogger(__name__)

# --- Carrega Configurações do .env ---
load_dotenv()
try:
    caldav_url = os.environ['CALDAV_URL']
    caldav_username = os.environ['CALDAV_USERNAME']
    caldav_password = os.environ['CALDAV_PASSWORD']
    caldav_calendar_name = os.environ['CALDAV_CALENDAR_NAME']
    default_timezone = os.getenv("DEFAULT_TIMEZONE", "America/Sao_Paulo")
    logger.info("Configurações CalDAV carregadas do .env")
except KeyError as e:
    logger.critical(f"Erro: Variável de ambiente {e} não encontrada no .env!")
    exit(1)

# --- Teste de Conexão e Instanciação ---
try:
    logger.info("--- Iniciando Teste CalDAV ---")
    logger.info(f"Tentando conectar a {caldav_url} com usuário {caldav_username}...")
    caldav_handler = CaldavHandler(
        url=caldav_url,
        username=caldav_username,
        password=caldav_password,
        calendar_name=caldav_calendar_name
    )
    logger.info("Conexão e instanciação do CaldavHandler bem-sucedidas.")
except ConnectionError as e:
    logger.critical(f"Falha CRÍTICA na conexão CalDAV: {e}", exc_info=True)
    exit(1)
except ValueError as e:
     logger.critical(f"Falha CRÍTICA ao encontrar calendário: {e}", exc_info=True)
     exit(1)
except Exception as e:
    logger.critical(f"Erro inesperado durante inicialização do CaldavHandler: {e}", exc_info=True)
    exit(1)

# --- Definições de Teste ---
tz = pytz.timezone(default_timezone)
now = datetime.datetime.now(tz)
test_patient_name = f"Teste Bot {uuid.uuid4().hex[:6]}" # Nome único para o teste
event_id_to_cancel = None # Será preenchido após o booking e find

# --- 1. Teste: Encontrar Slots Vagos ---
logger.info("\n--- TESTE 1: Encontrar Slots Vagos ---")
try:
    logger.info(f"Buscando 5 slots a partir de {now.isoformat()} (Seg/Ter 14-17h)")
    found_slots = caldav_handler.find_available_slots(
        start_search_dt=now,
        num_slots_to_find=5,
        consultation_duration_minutes=45, # Duração real da consulta
        block_duration_minutes=60,        # Bloco de 1h para checar
        preferred_days=[0, 1],            # Segunda e Terça
        valid_start_hours=[14, 15, 16, 17] # Horários de início
    )

    if found_slots:
        logger.info(f"Slots encontrados ({len(found_slots)}):")
        for i, slot in enumerate(found_slots):
            # Tenta formatar com locale, senão usa formato padrão
            try:
                import locale
                locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
                formatted_slot = slot.strftime("%A, %d de %B de %Y às %H:%M")
            except:
                formatted_slot = slot.strftime("%Y-%m-%d %H:%M:%S %Z%z")
            logger.info(f"  {i+1}. {formatted_slot} (ISO: {slot.isoformat()})")
        first_available_slot = found_slots[0] # Guarda o primeiro para tentar agendar
    else:
        logger.warning("Nenhum slot vago encontrado.")
        first_available_slot = None

except Exception as e:
    logger.error(f"Erro durante o teste de encontrar slots: {e}", exc_info=True)
    first_available_slot = None

# Pausa para visualização
time.sleep(2)

# --- 2. Teste: Agendar Consulta ---
logger.info("\n--- TESTE 2: Agendar Consulta ---")
if first_available_slot:
    try:
        start_time = first_available_slot
        end_time = start_time + datetime.timedelta(minutes=45) # Duração real
        logger.info(f"Tentando agendar '{test_patient_name}' em {start_time.isoformat()} até {end_time.isoformat()}")

        success, message = caldav_handler.book_appointment(
            start_time=start_time,
            end_time=end_time,
            patient_name=test_patient_name,
            patient_contact="55999999999", # Teste
            procedure_interest="Teste CalDAV" # Teste
        )

        if success:
            logger.info(f"Agendamento realizado com SUCESSO: {message}")
        else:
            logger.error(f"Falha no agendamento: {message}")

    except Exception as e:
        logger.error(f"Erro durante o teste de agendamento: {e}", exc_info=True)
else:
    logger.warning("Pulando teste de agendamento (nenhum slot encontrado no teste anterior).")

# Pausa para o servidor processar (se necessário)
time.sleep(3)

# --- 3. Teste: Encontrar Agendamento por Nome ---
logger.info("\n--- TESTE 3: Encontrar Agendamento por Nome ---")
try:
    search_start = now
    search_end = now + relativedelta(days=7) # Busca na próxima semana
    logger.info(f"Buscando agendamentos para '{test_patient_name}' entre {search_start.isoformat()} e {search_end.isoformat()}")

    found_appointments = caldav_handler.find_appointments_by_details(
        patient_name=test_patient_name,
        start_range=search_start,
        end_range=search_end
    )

    if found_appointments:
        logger.info(f"Agendamentos encontrados ({len(found_appointments)}):")
        for appt in found_appointments:
            start_f = appt['start'].strftime('%Y-%m-%d %H:%M') if appt.get('start') else 'N/A'
            end_f = appt['end'].strftime('%H:%M') if appt.get('end') else 'N/A'
            logger.info(f"  - ID: {appt.get('id', 'N/A')}, Summary: {appt.get('summary', 'N/A')}, Start: {start_f}, End: {end_f}")
            # Guarda o ID do primeiro encontrado para cancelar
            if not event_id_to_cancel:
                 event_id_to_cancel = appt.get('id')
                 logger.info(f"    (ID '{event_id_to_cancel}' será usado para o teste de cancelamento)")
    else:
        logger.warning(f"Nenhum agendamento encontrado para '{test_patient_name}' no período.")

except Exception as e:
    logger.error(f"Erro durante o teste de encontrar agendamentos: {e}", exc_info=True)

# Pausa
time.sleep(2)

# --- 4. Teste: Cancelar Agendamento ---
logger.info("\n--- TESTE 4: Cancelar Agendamento ---")
if event_id_to_cancel:
    try:
        logger.info(f"Tentando cancelar agendamento com ID: {event_id_to_cancel}")
        success, message = caldav_handler.cancel_appointment(event_identifier=event_id_to_cancel)

        if success:
            logger.info(f"Cancelamento realizado com SUCESSO: {message}")
        else:
            logger.error(f"Falha no cancelamento: {message}")

    except Exception as e:
        logger.error(f"Erro durante o teste de cancelamento: {e}", exc_info=True)
else:
    logger.warning("Pulando teste de cancelamento (nenhum ID de evento encontrado no teste anterior).")


logger.info("\n--- Teste CalDAV Concluído ---")