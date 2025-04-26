# caldav_handler.py (v1.4 - Revert Save Method, Strict VCALENDAR format)
import logging
import datetime
import pytz
from typing import List, Optional, Tuple, Dict, Any
import caldav
from caldav.elements import dav, cdav
from caldav.lib.error import NotFoundError
from dateutil.relativedelta import relativedelta
from dateutil.parser import parse as dateutil_parse
import uuid
import textwrap # Para formatar linhas longas no VCALENDAR

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = 'America/Sao_Paulo'
DEFAULT_SEARCH_MONTHS = 2

class CaldavHandler:
    """
    (v1.4) Gerencia a comunicação CalDAV.
    - Reverte para calendar.save_event(vcal_string).
    - Aplica formatação VCALENDAR mais rigorosa (quebra de linha, line ending).
    - Mantém correção da busca por nome (v1.3) e detecção de conflitos (v1.2).
    """
    # __init__, _connect, _get_tz, _is_connected (sem alterações da v1.3)
    def __init__(self, url: str, username: str, password: str, calendar_name: str):
        self.url = url
        self.username = username
        self.password = password
        self.calendar_name = calendar_name
        self.client = None
        self.principal = None
        self.calendar = None
        self._connect()

    def _connect(self):
        try:
            headers = {"User-Agent": "MargotClinicBot/1.0"}
            self.client = caldav.DAVClient(url=self.url, username=self.username, password=self.password, headers=headers)
            self.principal = self.client.principal()
            calendars = self.principal.calendars()
            self.calendar = next((c for c in calendars if c.name == self.calendar_name), None)
            if not self.calendar:
                calendar_names = [c.name for c in calendars]; logger.error(f"Calendário '{self.calendar_name}' não encontrado. Disponíveis: {calendar_names}")
                raise ValueError(f"Calendário '{self.calendar_name}' não encontrado.")
            logger.info(f"Conectado CalDAV: Calendário '{self.calendar_name}' OK.")
        except Exception as e:
            logger.error(f"Falha conexão/seleção CalDAV: {e}", exc_info=True); self.client = None; self.calendar = None
            raise ConnectionError(f"Não foi possível conectar/encontrar calendário CalDAV: {e}")

    def _get_tz(self) -> pytz.timezone:
        try: return pytz.timezone(DEFAULT_TIMEZONE)
        except pytz.UnknownTimeZoneError: logger.error(f"Timezone '{DEFAULT_TIMEZONE}' desconhecido! Usando UTC."); return pytz.utc

    def _is_connected(self) -> bool:
        if self.client and self.principal and self.calendar:
            try: self.principal.calendars(); return True
            except Exception as e: logger.warning(f"Possível desconexão CalDAV: {e}. Tentando reconectar...");
        try: self._connect(); return self.calendar is not None
        except ConnectionError: logger.error("Falha ao reconectar ao CalDAV."); return False

    # find_available_slots (sem alterações da v1.3)
    def find_available_slots(self,
                             start_search_dt: datetime.datetime, num_slots_to_find: int = 5,
                             consultation_duration_minutes: int = 45, block_duration_minutes: int = 60,
                             preferred_days: List[int] = [0, 1], valid_start_hours: List[int] = [14, 15, 16, 17]
                            ) -> List[datetime.datetime]:
        if not self._is_connected(): logger.error("find_available_slots: Não conectado."); return []
        tz = self._get_tz()
        start_search_dt = start_search_dt.astimezone(tz) if start_search_dt.tzinfo else tz.localize(start_search_dt)
        available_slots: List[datetime.datetime] = []
        current_check_date = start_search_dt.date()
        block_duration = datetime.timedelta(minutes=block_duration_minutes)
        search_limit_dt = start_search_dt + relativedelta(months=DEFAULT_SEARCH_MONTHS)
        logger.info(f"Iniciando busca por {num_slots_to_find} slots (blocos {block_duration_minutes}min) a partir de {start_search_dt.isoformat()}.")
        checked_days = 0; max_checked_days = 90
        while len(available_slots) < num_slots_to_find and current_check_date < search_limit_dt.date() and checked_days < max_checked_days:
            if current_check_date.weekday() not in preferred_days:
                current_check_date += datetime.timedelta(days=1); checked_days += 1; continue
            logger.debug(f"Verificando dia: {current_check_date.strftime('%Y-%m-%d')} (Dia Sem: {current_check_date.weekday()})")
            for hour in valid_start_hours:
                block_start_dt = tz.localize(datetime.datetime.combine(current_check_date, datetime.time(hour=hour)))
                block_end_dt = block_start_dt + block_duration
                if block_start_dt < start_search_dt: continue
                logger.debug(f"  - Verificando bloco: {block_start_dt.strftime('%H:%M')} - {block_end_dt.strftime('%H:%M')}")
                is_busy = False
                try:
                    # Busca eventos com margem maior (1 min antes/depois)
                    search_start = block_start_dt - datetime.timedelta(minutes=1)
                    search_end = block_end_dt + datetime.timedelta(minutes=1)
                    conflicting_events = self.calendar.search(start=search_start, end=search_end, event=True, expand=True)
                    event_summaries = []
                    # Checagem manual de sobreposição real
                    if conflicting_events:
                        for event in conflicting_events:
                            try:
                                vevent = event.instance.vevent
                                dtstart_obj = getattr(vevent, 'dtstart', None)
                                dtend_obj = getattr(vevent, 'dtend', None)
                                summary = getattr(vevent, 'summary', None)
                                if dtstart_obj and dtend_obj:
                                    start_val = dtstart_obj.value
                                    end_val = dtend_obj.value
                                    if isinstance(start_val, datetime.datetime) and isinstance(end_val, datetime.datetime):
                                        start_val = start_val.astimezone(tz) if start_val.tzinfo else tz.localize(start_val)
                                        end_val = end_val.astimezone(tz) if end_val.tzinfo else tz.localize(end_val)
                                        # Se houver sobreposição real
                                        if block_start_dt < end_val and block_end_dt > start_val:
                                            is_busy = True
                                            if summary:
                                                event_summaries.append(summary.value)
                                            else:
                                                event_summaries.append("[Sem Título]")
                                            break
                                # Se não tem dtstart/dtend, considera como ocupado por segurança
                                else:
                                    is_busy = True
                                    event_summaries.append("[Evento sem horário definido]")
                                    break
                            except Exception as e_event:
                                logger.warning(f"Erro ao verificar conflito com evento existente: {e_event}", exc_info=True)
                                is_busy = True
                                event_summaries.append("[Erro Summary]")
                                break
                        if is_busy:
                            logger.info(f"    * Bloco OCUPADO por: {', '.join(event_summaries) if event_summaries else '[Evento desconhecido]'}")
                        else:
                            logger.debug("    * Nenhum evento conflitante real.")
                    else:
                        logger.debug("    * Nenhum evento conflitante.")
                except Exception as e_caldav:
                    logger.error(f"    * Erro buscar conflitos: {e_caldav}", exc_info=True); is_busy = True
                if is_busy:
                    logger.debug(f"    * Bloco ocupado. Ignorando: {block_start_dt.isoformat()}")
                    continue

                available_slots.append(block_start_dt)
                logger.info(f"    * Bloco VAGO. Adicionando slot: {block_start_dt.isoformat()}")
            # Após verificar todos os horários do dia, verifica se já atingiu o número de slots desejados
            if len(available_slots) >= num_slots_to_find:
                break  # Essa verificação só é feita após avaliar todos os horários do dia
            current_check_date += datetime.timedelta(days=1); checked_days += 1
        if checked_days >= max_checked_days: logger.warning(f"Busca atingiu limite de {max_checked_days} dias.")
        logger.info(f"Busca finalizada. Encontrados {len(available_slots)} slots.")
        return available_slots

    # find_appointments_by_details (sem alterações da v1.3)
    def find_appointments_by_details(self,
                                     patient_name: str, start_range: datetime.datetime, end_range: datetime.datetime
                                    ) -> List[Dict[str, Any]]:
        if not self._is_connected():
            logger.error("find_appointments: Não conectado.")
            return []
        tz = self._get_tz()
        start_range = start_range.astimezone(tz) if start_range.tzinfo else tz.localize(start_range)
        end_range = end_range.astimezone(tz) if end_range.tzinfo else tz.localize(end_range)
        found_appointments = []
        logger.info(f"Buscando agendamentos para '{patient_name}' entre {start_range.isoformat()} e {end_range.isoformat()}")
        try:
            events = self.calendar.search(start=start_range, end=end_range, event=True, expand=True)
            for event in events:
                try:
                    vevent = event.instance.vevent
                    if not hasattr(vevent, 'summary'):
                        continue
                    summary = getattr(vevent, 'summary', None).value or ""
                    dtstart_obj = getattr(vevent, 'dtstart', None)
                    start_time = None
                    if dtstart_obj and hasattr(dtstart_obj, 'value'):
                        st_naive = dtstart_obj.value
                        if isinstance(st_naive, datetime.datetime):
                            start_time = st_naive.astimezone(tz) if st_naive.tzinfo else tz.localize(st_naive)
                        elif isinstance(st_naive, datetime.date):
                            start_time = tz.localize(datetime.datetime.combine(st_naive, datetime.time.min))
                    if patient_name.lower() in summary.lower() and start_time:
                        dtend_obj = getattr(vevent, 'dtend', None)
                        end_time = None
                        if dtend_obj and hasattr(dtend_obj, 'value'):
                            et_naive = dtend_obj.value
                            if isinstance(et_naive, datetime.datetime):
                                end_time = et_naive.astimezone(tz) if et_naive.tzinfo else tz.localize(et_naive)
                            elif isinstance(et_naive, datetime.date):
                                end_time = tz.localize(datetime.datetime.combine(et_naive, datetime.time.max))
                        # Novo filtro: ignora eventos sem hora final válida
                        if not end_time:
                            continue  # Ignora eventos sem hora final válida
                        event_id = event.url if hasattr(event, 'url') else (
                            getattr(vevent, 'uid', None).value if getattr(vevent, 'uid', None)
                            else event.id if hasattr(event, 'id') else None
                        )
                        if event_id:
                            appt = {"summary": summary, "start": start_time, "end": end_time, "id": str(event_id)}
                            found_appointments.append(appt)
                            logger.debug(f"  - Encontrado: ID={event_id}, Sum={summary}")
                        else:
                            logger.warning(f"  - Evento encontrado para '{patient_name}', sem ID.")
                except Exception as e_proc:
                    logger.error(f"  - Erro processando evento {getattr(event, 'url', 'sem URL')}: {e_proc}", exc_info=True)
        except Exception as e_search:
            logger.error(f"Erro durante calendar.search: {e_search}", exc_info=True)
        logger.info(f"Busca por '{patient_name}' finalizada. {len(found_appointments)} eventos encontrados.")
        found_appointments.sort(key=lambda x: x['start'])
        return found_appointments

    def _format_vcal_line(self, line: str) -> str:
        """Formata uma linha VCALENDAR com quebra e indentação (RFC 5545)."""
        # Garante que a linha termine com \r\n
        # Quebra linhas longas (máximo 75 octets recomendado, incluindo CRLF)
        folded_lines = textwrap.fill(line, width=73, subsequent_indent=' ', break_long_words=False, break_on_hyphens=False)
        return folded_lines + "\r\n"

    def book_appointment(self,
                         start_time: datetime.datetime, end_time: datetime.datetime,
                         patient_name: str, patient_contact: Optional[str] = None,
                         patient_email: Optional[str] = None,  # <<< Adicionado este parâmetro
                         indication_source: Optional[str] = None, procedure_interest: Optional[str] = None
                        ) -> Tuple[bool, str]:
        if not self._is_connected(): logger.error("book_appointment: Não conectado."); return False, "Sem conexão com calendário."
        if not start_time or not end_time:
            logger.error("Horário de início ou fim inválido para agendamento.")
            return False, "Horário de agendamento inválido. Não foi possível registrar o compromisso."
        if not self.calendar:
            logger.error("Calendário CalDAV não inicializado corretamente.")
            return False, "Erro interno no sistema de agenda."
        tz = self._get_tz()
        start_time = start_time.astimezone(tz) if start_time.tzinfo else tz.localize(start_time)
        end_time = end_time.astimezone(tz) if end_time.tzinfo else tz.localize(end_time)
        logger.debug(f"Verificando conflito final para {start_time.isoformat()} - {end_time.isoformat()}")
        try: # Checagem de conflito (sem alterações)
            conflict_check_start = start_time - datetime.timedelta(seconds=1)
            conflict_check_end = end_time + datetime.timedelta(seconds=1)
            events_found = self.calendar.search(start=conflict_check_start, end=conflict_check_end, event=True, expand=False)
            if events_found:
                summaries = [getattr(e.instance.vevent, 'summary', 'N/A').value for e in events_found if hasattr(e.instance.vevent, 'summary')]
                conflict_msg = f"Conflito final detectado ({len(events_found)}): {'; '.join(summaries)}."
                logger.warning(f"Falha agendamento {patient_name}: {conflict_msg}")
                return False, "Desculpe, este horário foi preenchido enquanto confirmávamos. Tente outro."
            logger.debug("Nenhum conflito final encontrado.")
        except Exception as e_conflict:
            logger.error(f"Erro verificando conflito final: {e_conflict}", exc_info=True); return False, "Erro ao verificar disponibilidade final."

        # Criação VCALENDAR com formatação mais rigorosa
        summary = f"Consulta - {patient_name}"
        desc_parts = [f"Paciente: {patient_name}"]
        if patient_contact: desc_parts.append(f"Contato: {patient_contact}")
        if patient_email: desc_parts.append(f"Email: {patient_email}")  # Adiciona o email
        if procedure_interest: desc_parts.append(f"Interesse: {procedure_interest}")
        if indication_source and indication_source.lower() not in ['não indicado', 'nao indicado', 'ninguem', 'ninguém', 'nao']:
             desc_parts.append(f"Indicação: {indication_source}")
        # Escapa caracteres especiais para VCALENDAR e mantém quebras de linha como \n
        description_escaped = "\\n".join(desc_parts).replace(',', '\\,').replace(';', '\\;').replace('\r\n','\\n').replace('\n','\\n')

        event_uid = f"{datetime.datetime.now(pytz.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4()}@clinicabot.margot"
        dt_format = "%Y%m%dT%H%M%S"
        tzid_str = "" if tz == pytz.utc else f";TZID={tz.zone}"
        dtstamp_utc = datetime.datetime.now(pytz.utc).strftime('%Y%m%dT%H%M%SZ')

        # Monta a string VCALENDAR linha a linha, formatando cada uma
        vcal_string = ""
        vcal_string += self._format_vcal_line("BEGIN:VCALENDAR")
        vcal_string += self._format_vcal_line("VERSION:2.0")
        vcal_string += self._format_vcal_line("PRODID:-//Clinicabot//Margot Agendamento v1.4//PT")
        vcal_string += self._format_vcal_line("CALSCALE:GREGORIAN")
        vcal_string += self._format_vcal_line("BEGIN:VEVENT")
        vcal_string += self._format_vcal_line(f"UID:{event_uid}")
        vcal_string += self._format_vcal_line(f"DTSTAMP:{dtstamp_utc}")
        vcal_string += self._format_vcal_line(f"DTSTART{tzid_str}:{start_time.strftime(dt_format)}")
        vcal_string += self._format_vcal_line(f"DTEND{tzid_str}:{end_time.strftime(dt_format)}")
        vcal_string += self._format_vcal_line(f"SUMMARY:{summary}")
        # Aplica formatação à descrição (pode ser longa)
        vcal_string += self._format_vcal_line(f"DESCRIPTION:{description_escaped}")
        vcal_string += self._format_vcal_line("STATUS:CONFIRMED")
        vcal_string += self._format_vcal_line("TRANSP:OPAQUE")
        vcal_string += self._format_vcal_line("SEQUENCE:0")
        vcal_string += self._format_vcal_line("END:VEVENT")
        vcal_string += self._format_vcal_line("END:VCALENDAR")

        try:
            # **VOLTA A USAR save_event com a string formatada**
            logger.info(f"Tentando salvar agendamento (Método save_event) para '{patient_name}' UID: {event_uid}")
            # logger.debug(f"VCALENDAR Content:\n{vcal_string}") # Descomente para debug pesado do VCAL
            new_event = self.calendar.save_event(vcal_string)
            event_ref = new_event.url if hasattr(new_event, 'url') else event_uid # Tenta pegar URL
            logger.info(f"Agendamento (save_event) CRIADO com sucesso. Ref: {event_ref}")
            try: # Format date for message
                import locale; locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
                dt_f = start_time.strftime('%A, %d de %B às %H:%M')
            except: dt_f = start_time.strftime('%d/%m/%Y %H:%M')
            return True, f"Agendamento confirmado para {patient_name} em {dt_f}."

        except Exception as e_caldav_save:
            logger.error(f"Erro CRÍTICO ao salvar evento (save_event) UID {event_uid}: {e_caldav_save}", exc_info=True)
            # Log específico se o erro antigo reaparecer
            if "Unexpected value None for self.url" in str(e_caldav_save):
                 logger.error(">>> O erro 'Unexpected value None for self.url' OCORREU com save_event(string).")
            return False, "Erro técnico ao registrar agendamento. Equipe notificada."

    # cancel_appointment (com checagem manual de dtstart/dtend)
    def cancel_appointment(self, event_identifier: str) -> Tuple[bool, str]:
        if not self._is_connected():
            logger.error("cancel_appointment: Não conectado.")
            return False, "Sem conexão com calendário."
        if not event_identifier:
            logger.error("Cancelamento sem ID.")
            return False, "Não identificamos qual agendamento cancelar."
        logger.info(f"Tentando cancelar evento ID: {event_identifier}")
        try:
            event_to_delete = None
            try:  # Busca por ID (URL ou UID)
                if isinstance(event_identifier, caldav.objects.Event):
                    event_to_delete = event_identifier
                elif isinstance(event_identifier, str):
                    if "http" in event_identifier:
                        event_to_delete = self.calendar.event_by_url(event_identifier)
                    else:
                        event_to_delete = self.calendar.event_by_uid(event_identifier)
                # --- Checagem manual de dtstart/dtend após obtenção do evento ---
                if event_to_delete:
                    vevent = event_to_delete.instance.vevent
                    dtstart_obj = getattr(vevent, 'dtstart', None)
                    dtend_obj = getattr(vevent, 'dtend', None)
                    if not dtstart_obj or not dtend_obj:
                        logger.warning("Evento sem dtstart ou dtend. Cancelamento interrompido por segurança.")
                        return False, "Erro ao localizar informações completas do agendamento para cancelamento."
            except NotFoundError:
                logger.warning(f"Evento ID '{event_identifier}' não encontrado para cancelar.")
                return False, "Agendamento não encontrado."
            except Exception as e_find:
                logger.error(f"Erro ao buscar evento ID '{event_identifier}': {e_find}", exc_info=True)
                return False, "Erro ao localizar agendamento."
            if event_to_delete:
                try:
                    summary = "Agendamento"
                    start_str = ""
                    try:  # Pega detalhes ANTES de deletar
                        vevent = event_to_delete.instance.vevent
                        summary = getattr(vevent, 'summary', 'Agendamento').value
                        dtstart_obj = getattr(vevent, 'dtstart', None)
                        if dtstart_obj and hasattr(dtstart_obj, 'value'):
                            st_naive = dtstart_obj.value
                            if isinstance(st_naive, datetime.datetime):
                                tz = self._get_tz()
                                start_time = st_naive.astimezone(tz) if st_naive.tzinfo else tz.localize(st_naive)
                                try:
                                    import locale
                                    locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
                                    start_str = f" de {start_time.strftime('%A, %d/%m às %H:%M')}"
                                except:
                                    start_str = f" de {start_time.strftime('%d/%m %H:%M')}"
                    except Exception as e_details:
                        logger.warning(f"Erro lendo detalhes evento {event_identifier}: {e_details}")
                    event_to_delete.delete()  # DELETA
                    logger.info(f"Evento '{summary}' (ID: {event_identifier}) cancelado.")
                    return True, f"{summary}{start_str} foi cancelado com sucesso."
                except Exception as e_delete:
                    logger.error(f"Erro ao DELETAR evento ID '{event_identifier}': {e_delete}", exc_info=True)
                    return False, "Erro ao remover agendamento."
            else:
                logger.warning(f"Evento ID '{event_identifier}' não encontrado (após busca).")
                return False, "Agendamento não encontrado."
        except Exception as e_general:
            logger.error(f"Erro inesperado cancelamento ID '{event_identifier}': {e_general}", exc_info=True)
            return False, "Erro inesperado no cancelamento."
    # (v1.4) Adiciona proteção para reagendamento: checagem de conexão e horários válidos
    def reagendar_appointment(self, old_event_id: str,
                              start_time: datetime.datetime, end_time: datetime.datetime,
                              patient_name: str, patient_contact: Optional[str] = None,
                              indication_source: Optional[str] = None, procedure_interest: Optional[str] = None
                             ) -> Tuple[bool, str]:
        if not self._is_connected():
            logger.error("reagendar_appointment: Não conectado.")
            return False, "Sem conexão com calendário."
        if not start_time or not end_time:
            logger.error("Horário de início ou fim inválido para reagendamento.")
            return False, "Horário de reagendamento inválido. Não foi possível atualizar o compromisso."
        # Cancela o anterior
        cancel_ok, cancel_msg = self.cancel_appointment(old_event_id)
        if not cancel_ok:
            return False, f"Não foi possível cancelar o compromisso anterior: {cancel_msg}"
        # Agenda o novo
        book_ok, book_msg = self.book_appointment(
            start_time, end_time, patient_name, patient_contact, indication_source, procedure_interest
        )
        if book_ok:
            return True, "Compromisso reagendado com sucesso."
        else:
            return False, f"Erro ao reagendar: {book_msg}"
