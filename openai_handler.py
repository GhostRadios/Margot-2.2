# openai_handler.py (v6.7 - Contexto para Coleta de Dados)
import os
import logging
import datetime
import locale
import json
from openai import OpenAI, APIError, RateLimitError
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Mantido da versão anterior
MAX_HISTORY_PAIRS = 5

class OpenAIHandler:
    """
    (v6.7) Gerencia a interação com a API OpenAI.
    - Persona v6.6 mantida (sem markdown, saudação inicial fixa, RAG conversacional).
    - Adiciona contexto ao prompt para ajudar na coleta sequencial de dados
      e lidar com respostas inesperadas do usuário durante esse processo.
    """
    def __init__(self, api_key: str):
        if not api_key:
            logger.error("API Key da OpenAI não fornecida.")
            raise ValueError("API Key da OpenAI é necessária.")
        try:
            self.client = OpenAI(api_key=api_key)
            self.model = "gpt-3.5-turbo" # Mantendo gpt-3.5-turbo
            logger.info(f"Cliente OpenAI inicializado com sucesso: Modelo {self.model}")
        except Exception as e:
            logger.error(f"Falha ao inicializar cliente OpenAI: {e}", exc_info=True)
            raise

    def _define_system_prompt(self) -> str:
        """Define a persona e as regras de comportamento da Margot (v6.7)."""
        # Obtém anos dinamicamente para usar no prompt
        year_formation, year_plastic_spec = self._get_dynamic_years_from_json()

        persona = (
            f"Você é Margot, a concierge e responsável pelo atendimento aos pacientes na Clínica Missel, cujo especialista responsável é o Dr. Juarez Missel (formado em {year_formation}, especialista em plástica desde {year_plastic_spec}), "
            "um renomado especialista em Cirurgia Plástica. "
            "Seu nome é Margot. Sua comunicação deve ser impecável: extremamente educada, cordial, acolhedora, paciente, atenciosa e altamente prestativa. Aja sempre como um membro humano da equipe. "
            "Trate todos os pacientes com respeito e empatia."
            "Seu objetivo principal é fornecer informações precisas sobre a Clínica Missel, os serviços oferecidos, o Dr. Juarez Missel e, crucialmente, gerenciar a agenda de consultas: marcar novos horários, "
            "ajudar pacientes a remarcar ou cancelar consultas existentes, sempre seguindo o fluxo definido pelo sistema (você será informada do estado atual do agendamento e qual informação pedir)."
            "\n**Instruções Gerais:**\n"
            "- Responda sempre em Português do Brasil.\n"
            "- **NUNCA use formatação Markdown (como asteriscos para negrito ou itálico) em suas respostas finais. Use apenas texto puro.**\n"
            "- Use uma linguagem clara, formal na medida certa, mas acolhedora e nunca robótica.\n"
            "- Use despedidas cordiais como 'Tenha um excelente dia!', 'Qualquer outra dúvida, estou à disposição!', 'A Clínica Missel agradece seu contato. Até breve!'.\n"
            "\n**Regras Essenciais:**\n"
            "1.  **APRESENTAÇÃO INICIAL:** Na **PRIMEIRA** mensagem que você envia nesta conversa, **OBRIGATORIAMENTE** apresente-se como 'Olá! Sou a Margot, da Clínica Missel. Em que posso ajudar?'. Em **TODAS** as respostas **SEGUINTES** (a partir da segunda), **NÃO** repita mais essa apresentação completa. Vá direto ao ponto da pergunta do usuário ou use uma saudação curta e apropriada se necessário. Para responder perguntas casuais como 'Tudo bem?', responda naturalmente sem a apresentação completa.\n"
            "2.  **VOCÊ NÃO É IA:** **NUNCA** revele ser uma inteligência artificial, chatbot, programa ou modelo. Você é Margot, uma pessoa da equipe.\n"
            "3.  **SEM CONSELHOS MÉDICOS:** **NÃO** dê diagnósticos, opiniões médicas diretas, prognósticos. Redirecione sempre para a consulta individual com o Dr. Juarez.\n"
            "4.  **INFORMAÇÕES DA CLÍNICA (RAG):** Use as informações da base de dados (quando fornecidas no contexto 'INFORMAÇÃO DA BASE DE DADOS') sobre endereço, horários, telefone, procedimentos, convênios, links, etc. Siga as instruções do prompt RAG sobre como apresentar esses dados (completude, formato, restrições, custos, etc.). Se a informação não estiver na base, diga que verificará ou que será detalhado na consulta.\n"
            # ---> MODIFICAÇÃO AQUI: Instrução sobre fluxo de agendamento <---
            "5.  **FLUXO DE AGENDAMENTO (IMPORTANTE):**\n"
            "    a. **Coleta de Dados:** Quando o 'Contexto de Agendamento' indicar que você precisa de uma informação específica (ex: `expected_data='phone'`), sua resposta **DEVE** pedir **APENAS** essa informação de forma clara e educada. Se o usuário responder com algo que não é a informação pedida, reconheça brevemente o que ele disse (se apropriado e curto), mas **REPITA** educadamente o pedido da informação que você ainda precisa. Exemplo: Se você pediu o telefone e o usuário falou do tempo, responda algo como: 'Entendo. Para continuarmos com o agendamento, ainda preciso do seu número de telefone com DDD, por favor.'\n"
            "    b. **Apresentação de Horários:** Quando o contexto fornecer 'Horários Disponíveis Encontrados', você deve formatá-los claramente, numerados, e pedir ao usuário para escolher pelo número. Ex: 'Encontrei estes horários: \n1. Segunda, 29/07 às 14:00\n2. Terça, 30/07 às 15:00\n...\nQual número você prefere?'\n"
            "    c. **Confirmação:** Quando o contexto indicar 'Confirmar Horário Escolhido', sua resposta deve apresentar o horário selecionado e pedir uma confirmação direta (Sim/Não). Ex: 'Perfeito! Podemos confirmar sua consulta para Segunda, 29 de Julho às 14:00? (Sim/Não)'\n"
            "    d. **Resultado do Agendamento:** Quando o contexto informar 'Resultado do Agendamento (Sucesso/Falha/Erro)', formule uma mensagem clara informando o resultado ao usuário, usando os detalhes fornecidos no contexto.\n"
            "    e. **Cancelamento/Reagendamento:** Siga as instruções contextuais para confirmar qual agendamento o usuário deseja alterar e informe o resultado da operação (cancelamento/remarcação).\n"
            "    f. **Siga o Fluxo:** O sistema (`main.py`) controlará o estado (`scheduling_status`). Sua tarefa é gerar a resposta apropriada para CADA estado, conforme instruído pelo contexto.\n"
            # ---> FIM DA MODIFICAÇÃO <---
            "6.  **ERROS/INDISPONIBILIDADE:** Se não puder fazer algo ou se o sistema informar um erro (ex: erro ao buscar slots, erro ao agendar), explique o motivo claramente ao usuário (baseado na mensagem de erro fornecida no contexto, se houver) e ofereça alternativas ou diga que a equipe entrará em contato, se apropriado.\n"
            "7.  **TOM DE VOZ:** Mantenha sempre a positividade, profissionalismo e disposição para ajudar ('Será um prazer!', 'Com certeza!', 'Estou à disposição!').\n"
            "8.  **FOCO:** Mantenha a conversa nos serviços da Clínica Missel. Se o usuário desviar muito (e não estiver no fluxo de agendamento), redirecione gentilmente: 'Compreendo, mas meu foco aqui é auxiliar com os assuntos da Clínica Missel. Posso ajudar com informações sobre nossos procedimentos ou agendamentos?'.\n"
            # ---> NOVA REGRA <---
            "9.  **EVITE AÇÕES DIRETAS NO TEXTO:** Não inclua marcadores como `[ACTION:FIND_SLOTS]` ou `[ACTION:BOOK_APPOINTMENT]` em suas respostas finais ao usuário. O sistema interpretará a necessidade dessas ações com base no estado da conversa e nas suas respostas contextuais.\n"
            "4.  DATAS/ANOS: Lembre-se que o Dr. Juarez Missel é formado desde {year_formation} e especialista em plástica desde {year_plastic_spec} (use esses anos diretamente se relevante).\n"
        )
        return persona

    # Função _get_dynamic_years_from_json (Inalterada - já estava na v6.6)
    def _get_dynamic_years_from_json(self) -> tuple[int, int]:
        """Tenta ler os anos de formação e especialização do JSON, com tratamento de erro."""
        default_formation_year = 1998
        default_plastic_spec_year = 2004
        formation_year = default_formation_year
        plastic_spec_year = default_plastic_spec_year
        json_path = "knowledge_base.json"
        try:
            if not os.path.exists(json_path):
                 logger.warning(f"Arquivo {json_path} não encontrado para buscar anos dinâmicos.")
                 return default_formation_year, default_plastic_spec_year

            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            doctor_info = data.get("doctor_info", {})
            if not isinstance(doctor_info, dict):
                 logger.warning("'doctor_info' não é um dicionário no JSON.")
                 return default_formation_year, default_plastic_spec_year

            # Ano de Graduação
            grad_info = doctor_info.get("graduation", {})
            if isinstance(grad_info, dict):
                grad_year_str = grad_info.get("year")
                if grad_year_str:
                    try:
                        formation_year = int(grad_year_str)
                    except (ValueError, TypeError):
                        logger.warning(f"Valor inválido para ano de graduação: {grad_year_str}. Usando padrão {default_formation_year}.")
                else:
                    logger.warning(f"Ano de graduação não encontrado. Usando padrão {default_formation_year}.")
            else:
                 logger.warning("'graduation' não é um dicionário. Usando padrão {default_formation_year}.")


            # Ano de Especialização em Plástica
            post_grad = doctor_info.get("post_graduation", [])
            found_plastic_spec = False
            if isinstance(post_grad, list):
                for spec in post_grad:
                    if not isinstance(spec, dict): continue
                    specialty_lower = spec.get("specialty", "").lower()
                    if "plástica" in specialty_lower or "plastica" in specialty_lower:
                        comp_year_val = spec.get("completion_year")
                        if comp_year_val:
                            try:
                                plastic_spec_year = int(comp_year_val)
                                found_plastic_spec = True
                                logger.debug(f"Ano de especialização em plástica encontrado: {plastic_spec_year}")
                                break
                            except (ValueError, TypeError):
                                logger.warning(f"Valor inválido para ano de conclusão plástica: {comp_year_val}. Usando padrão {default_plastic_spec_year}.")
                        else:
                             logger.warning(f"Especialização plástica encontrada, mas sem ano. Usando padrão {default_plastic_spec_year}.")

            if not found_plastic_spec:
                 logger.warning(f"'post_graduation' não é lista ou não contém plástica com ano. Usando padrão {default_plastic_spec_year}.")

            # logger.debug(f"Anos lidos/definidos: Formação={formation_year}, Plástica={plastic_spec_year}")
            return formation_year, plastic_spec_year

        except (FileNotFoundError, json.JSONDecodeError) as e:
             logger.warning(f"Não foi possível ler {json_path} para anos dinâmicos: {e}")
             return default_formation_year, default_plastic_spec_year
        except Exception as e:
             logger.error(f"Erro inesperado ao ler anos do JSON ({json_path}): {e}", exc_info=True)
             return default_formation_year, default_plastic_spec_year

    # ---> ASSINATURA MODIFICADA: Adicionado expected_data e outros contextos <---
    def get_chat_response(self,
                          user_message: str,
                          conversation_history: Optional[List[Dict[str, str]]] = None,
                          # Contextos específicos passados pelo main.py
                          current_schedule_state: Optional[str] = None,
                          patient_data: Optional[Dict[str, Any]] = None,
                          expected_data: Optional[str] = None, # Ex: 'name', 'phone', 'email', 'procedure', 'indication', 'slot_choice', 'confirmation'
                          relevant_knowledge: Optional[str] = None, # RAG
                          available_slots_context: Optional[List[Dict[str, str]]] = None, # Lista formatada de slots
                          chosen_slot_context: Optional[str] = None, # Slot escolhido formatado para confirmação
                          booking_result_context: Optional[str] = None, # 'success', 'failure', 'error_finding_slots', etc.
                          booking_message_detail: Optional[str] = None, # Mensagem de erro/sucesso do CalDAV
                          cancel_rebook_context: Optional[Dict[str, Any]] = None # Detalhes sobre cancelamento/reagendamento
                         ) -> str:
        """
        (v6.7) Gera a resposta da OpenAI com base na persona, histórico, RAG e CONTEXTO DO AGENDAMENTO.
        """
        if not user_message or not user_message.strip():
             logger.warning("Recebida mensagem de usuário vazia.")
             return "Desculpe, não entendi sua mensagem."

        system_prompt = self._define_system_prompt()
        messages = [{"role": "system", "content": system_prompt}]

        # Contexto Temporal (Inalterado)
        try: locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
        except locale.Error:
            try: locale.setlocale(locale.LC_TIME, 'Portuguese_Brazil.1252');
            except locale.Error: logger.warning("Locale pt_BR não definido.");
        now = datetime.datetime.now(); data_hora_formatada = now.strftime("%Y-%m-%d %H:%M");
        try: data_hora_formatada = now.strftime("%A, %d de %B de %Y, %H:%M");
        except Exception: logger.warning(f"Usando formato data/hora fallback.");
        messages.append({"role": "system", "content": f"Contexto Temporal: A data e hora atuais são {data_hora_formatada}."})

        # --- Injeção de Contexto de Agendamento ---
        context_parts = []
        context_parts.append(f"Estado Atual do Agendamento: {current_schedule_state or 'Nenhum (Conversa Geral)'}")
        if patient_data:
            # Não envia a lista inteira, apenas os dados já coletados
            collected_data = {k: v for k, v in patient_data.items() if v}
            if collected_data:
                context_parts.append(f"Dados do Paciente já coletados: {json.dumps(collected_data, ensure_ascii=False)}")

        # ---> INFORMAÇÃO ESPERADA <---
        if expected_data:
             data_map = {
                 'name': 'o nome completo do paciente',
                 'phone': 'o número de telefone com DDD',
                 'email': 'o endereço de e-mail',
                 'procedure': 'o procedimento de interesse ou se é consulta geral',
                 'indication': 'se houve indicação e por quem',
                 'slot_choice': 'que o usuário escolha UM dos horários da lista APENAS PELO NÚMERO',
                 'confirmation': 'uma confirmação (Sim/Não) para o horário selecionado',
                 'cancel_confirmation': 'uma confirmação (Sim/Não) para CANCELAR o agendamento encontrado',
                 'rebook_confirmation': 'uma confirmação (Sim/Não) para REMARCAR o agendamento encontrado',
                 'multiple_choice': 'que o usuário escolha UM dos agendamentos da lista APENAS PELO NÚMERO para cancelar/remarcar'
             }
             expected_readable = data_map.get(expected_data, f"a informação '{expected_data}'")
             context_parts.append(f"**Ação Requerida AGORA:** Pedir {expected_readable}. Se o usuário responder outra coisa, **REPITA** o pedido educadamente.")

        # ---> OUTROS CONTEXTOS <---
        if available_slots_context:
             context_parts.append(f"Horários Disponíveis Encontrados (Apresente numerados e peça para escolher UM número): {json.dumps(available_slots_context, ensure_ascii=False)}")
        if chosen_slot_context:
             context_parts.append(f"Confirmar Horário Escolhido (Peça Sim/Não): {chosen_slot_context}")
        if booking_result_context:
             context_parts.append(f"Resultado do Agendamento: {booking_result_context}")
             if booking_message_detail:
                  context_parts.append(f"Detalhe do Resultado: {booking_message_detail}") # Ex: Mensagem de erro do CalDAV
        if cancel_rebook_context:
             context_parts.append(f"Contexto de Cancelamento/Reagendamento: {json.dumps(cancel_rebook_context, ensure_ascii=False)}") # Passa detalhes encontrados, etc.

        # Adiciona o bloco de contexto de agendamento ao prompt
        if len(context_parts) > 1: # Só adiciona se tiver mais que o estado atual
            scheduling_context_prompt = "\n".join(context_parts)
            messages.append({"role": "system", "content": f"--- Contexto de Agendamento ---\n{scheduling_context_prompt}\n-----------------------------"})
            logger.info("Adicionando Contexto de Agendamento ao prompt.")
        # --- Fim da Injeção de Contexto ---


        # Adiciona histórico da conversa, limitado (Inalterado)
        if conversation_history:
            messages.extend(conversation_history[-(MAX_HISTORY_PAIRS * 2):])

        # Adiciona contexto RAG, se disponível (Inalterado - v6.6 já tinha RAG conversacional)
        if relevant_knowledge:
            # O prompt RAG da v6.6 já é adequado
            knowledge_context_prompt = (
                f"INFORMAÇÃO DA BASE DE DADOS (Use OBRIGATORIAMENTE e COMPLETAMENTE):\n"
                f"------------------------------------------------------------------\n"
                f"{relevant_knowledge}\n"
                f"------------------------------------------------------------------\n\n"
                f"SUA TAREFA, MARGOT (Siga estas instruções E as Regras Essenciais):\n"
                f"1.  Responda à ÚLTIMA mensagem do usuário (abaixo) usando a informação da base de dados fornecida acima.\n"
                f"2.  SE A INFORMAÇÃO ACIMA COMEÇAR COM '**Detalhes sobre...**' (é um procedimento):\n" # Ajustado para o formato do KH v8.6
                f"    a. Inicie confirmando que o procedimento é realizado.\n"
                f"    b. Integre a '**Descrição:**' completa do procedimento de forma natural e fluida.\n"
                f"    c. **DIFERENCIAIS CONVERSACIONAIS (MUITO IMPORTANTE):** Apresente **TODOS** os pontos listados sob '**Diferenciais e Informações Adicionais:**' (Clínico/Técnica, Dr. Juarez Missel, Artigos, Capítulos, Prêmios, etc., SE PRESENTES) **INTEGRANDO-OS NATURALMENTE EM UM OU MAIS PARÁGRAFOS**. **NÃO use listas ou marcadores (-, *) para apresentar os diferenciais**. Em vez disso, conecte as ideias de forma conversacional. Por exemplo: 'Um dos diferenciais importantes é a técnica clínica utilizada, que [explicar]. Além disso, a vasta experiência do Dr. Juarez Missel nesse tipo de cirurgia, [explicar], contribui para os resultados. Ele também publicou artigos sobre o tema, como [citar artigo], e contribuiu com o capítulo de livro [citar livro]...'. **Use TODOS os pontos de diferenciais fornecidos, mas em formato de texto corrido e explicativo.**\n"
                f"    d. Se houver uma '**Atenção Importante:**', mencione-a claramente.\n"
                f"    e. **OBJETIVO CRÍTICO:** Sua resposta sobre o procedimento deve ser **EXAUSTIVA e FLUIDA**, utilizando **TODA** a informação relevante fornecida (Descrição, Atenção, e **TODOS** os Diferenciais integrados conversacionalmente). **NÃO RESUMA NADA IMPORTANTE.**\n"
                f"3.  SE A INFORMAÇÃO ACIMA FOR OUTRO TÓPICO (Não um procedimento): Apresente a informação de forma clara, completa e conversacional, mantendo seu tom cordial.\n"
                f"4.  DATAS/ANOS: Lembre-se que o Dr. Juarez Missel é formado desde {1998} e especialista em plástica desde {2004} (use esses anos diretamente se relevante).\n"
                f"5.  Formatação de Lista de Procedimentos GERAL: Mantenha a regra anterior (usar '-' apenas se a informação começar EXATAMENTE com '**Principais Procedimentos Realizados:**').\n"
                f"6.  Convênios/Custos/etc.: Siga as Regras Essenciais e a informação específica fornecida.\n"
                f"7.  Ignore esta seção se a pergunta não tiver relação direta com a informação."
            )
            messages.append({"role": "system", "content": knowledge_context_prompt})
            logger.info("Adicionando contexto RAG ao prompt.")
        # else: # Removido o log aqui para não poluir tanto
             # logger.info("Não foi encontrado conhecimento relevante. Usando apenas persona, histórico e contexto de agendamento.")


        # Adiciona a mensagem atual do usuário
        messages.append({"role": "user", "content": user_message})

        logger.debug(f"Enviando para OpenAI ({self.model}). Hist: {len(conversation_history) if conversation_history else 0}. "
                     f"RAG: {'Sim' if relevant_knowledge else 'Não'}. Estado: {current_schedule_state}. "
                     f"Contextos: ExpData={expected_data}, Slots={bool(available_slots_context)}, Chosen={bool(chosen_slot_context)}, "
                     f"Result={booking_result_context}, CnclRebook={bool(cancel_rebook_context)}. Total msgs: {len(messages)}")
        # Descomente para debug pesado do prompt completo:
        # try:
        #      logger.debug(f"Prompt completo para OpenAI: {json.dumps(messages, indent=2, ensure_ascii=False)}")
        # except Exception as e_json:
        #      logger.error(f"Erro ao serializar prompt para debug: {e_json}")


        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.4, # Levemente reduzido para respostas mais focadas no fluxo
                max_tokens=1000, # Reduzido um pouco, respostas de fluxo costumam ser menores
                presence_penalty=0.1,
                frequency_penalty=0.1
            )
            assistant_response = response.choices[0].message.content
            if response.usage:
                 logger.info(f"OpenAI OK. Usage: P={response.usage.prompt_tokens}, C={response.usage.completion_tokens}, T={response.usage.total_tokens}")
            else:
                 logger.info("OpenAI OK (usage não reportado).")

            if assistant_response and assistant_response.strip():
                # Remove quaisquer asteriscos duplos restantes como uma garantia extra (mantido)
                final_response = assistant_response.strip().replace('**', '')
                # Remove marcadores de ação se a IA ainda os incluir por engano
                final_response = final_response.replace('[ACTION:FIND_SLOTS]', '').replace('[ACTION:BOOK_APPOINTMENT]', '').strip()
                return final_response
            else:
                logger.warning("OpenAI retornou resposta vazia.")
                return "Peço desculpas, não consegui gerar uma resposta adequada neste momento. Poderia repetir, por favor?"

        # Tratamento de erros (Inalterado)
        except RateLimitError as e:
             logger.error(f"Erro Rate Limit OpenAI: {e}")
             # Mensagem mais útil para o usuário final
             return "Desculpe, estamos com muitas solicitações no momento. Por favor, tente novamente em alguns instantes."
        except APIError as e:
             logger.error(f"Erro API OpenAI: {e}", exc_info=True)
             return "Desculpe, tivemos um problema técnico com nossa assistente virtual. A equipe já foi notificada. Por favor, tente novamente mais tarde."
        except Exception as e:
             logger.error(f"Erro inesperado OpenAI: {e}", exc_info=True)
             return "Desculpe, ocorreu um erro inesperado ao processar sua solicitação. Por favor, tente novamente."

# Bloco de teste local (mantido)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Erro: OPENAI_API_KEY não definida.")
    else:
        print("Inicializando OpenAIHandler para teste...")
        handler = OpenAIHandler(api_key=api_key)
        print("Handler inicializado.")

        # --- Exemplo de Teste: Simular Re-prompt de Telefone ---
        print("\n--- Teste: Re-prompt Telefone ---")
        history_test = [
            {"role": "user", "content": "Quero agendar uma consulta"},
            {"role": "assistant", "content": "Claro! Para começar, qual seu nome completo?"},
            {"role": "user", "content": "Meu nome é Fulano de Tal"},
            {"role": "assistant", "content": "Obrigada, Fulano! Agora, por favor, me informe o seu número de telefone com DDD."}
        ]
        user_msg_test = "Ah, esqueci de perguntar, qual o valor da consulta mesmo?"
        response = handler.get_chat_response(
            user_message=user_msg_test,
            conversation_history=history_test,
            current_schedule_state="awaiting_phone",
            patient_data={"name": "Fulano de Tal"},
            expected_data="phone" # <--- Indicando que esperamos o telefone
        )
        print(f"Histórico:\n{json.dumps(history_test, indent=2, ensure_ascii=False)}")
        print(f"Usuário diz: {user_msg_test}")
        print(f"Margot (esperado re-prompt): {response}")

        # --- Exemplo de Teste: Apresentar Slots ---
        print("\n--- Teste: Apresentar Slots ---")
        slots_test = [
             {"index": 1, "datetime": "2024-08-05T14:00:00-03:00", "formatted": "Segunda-feira, 05 de Agosto às 14:00"},
             {"index": 2, "datetime": "2024-08-05T15:00:00-03:00", "formatted": "Segunda-feira, 05 de Agosto às 15:00"},
             {"index": 3, "datetime": "2024-08-06T14:00:00-03:00", "formatted": "Terça-feira, 06 de Agosto às 14:00"}
        ]
        response_slots = handler.get_chat_response(
            user_message="Ok, pode me mostrar os horários", # Mensagem irrelevante aqui, contexto manda
            conversation_history=[],
            current_schedule_state="awaiting_choice",
            patient_data={"name": "Ciclana", "phone": "11999998888", "email":"c@c.com", "procedure":"Consulta Geral", "indication":"Amiga"},
            available_slots_context=slots_test # <--- Passando os slots formatados
        )
        print(f"Contexto: Apresentar slots {json.dumps(slots_test, indent=2, ensure_ascii=False)}")
        print(f"Margot (esperado apresentar slots e pedir número): {response_slots}")