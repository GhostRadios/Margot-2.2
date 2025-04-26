# knowledge_handler.py (v8.6 - Context-Aware RAG + Correções)
import json
import logging
import os
import re
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Lista expandida para detecção de seguro
KNOWN_INSURANCE_NAMES = ["unimed", "bradesco", "sulamerica", "amil", "porto seguro", "ipsm", "ipê", "saúde caixa", "geap", "cass"]

class KnowledgeHandler:
    """
    (v8.6) Carrega a base de conhecimento, busca informações COMPLETAS para RAG
    e utiliza o histórico da conversa para entender o contexto.
    """
    def __init__(self, json_file_path: str = "knowledge_base.json"):
        self.file_path = json_file_path
        self.data: Optional[Dict[str, Any]] = self._load_knowledge()
        self._procedure_map: Dict[str, Dict[str, Any]] = {}
        self._procedure_search_terms: Dict[str, List[str]] = {}
        self._procedure_variation_map: Dict[str, str] = {} # Map: variation_lower -> original_name_lower

        if self.data:
            self._build_procedure_indexes()
        else:
            logger.error(f"Base de conhecimento em {self.file_path} não pôde ser carregada. RAG estará inoperante.")

    def _load_knowledge(self) -> Optional[Dict[str, Any]]:
        """Carrega a base de conhecimento do arquivo JSON."""
        if not os.path.exists(self.file_path):
            logger.error(f"Arquivo da base de conhecimento não encontrado: {self.file_path}")
            return None
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                knowledge_data = json.load(f)
            logger.info(f"Base de conhecimento carregada com sucesso de: {self.file_path}")
            # Validação mínima da estrutura esperada
            if not isinstance(knowledge_data.get("procedures"), list) or \
               not isinstance(knowledge_data.get("clinic_info"), dict) or \
               not isinstance(knowledge_data.get("doctor_info"), dict):
                logger.warning("Estrutura básica do JSON (procedures, clinic_info, doctor_info) parece inválida.")
                # Poderia retornar None aqui se a estrutura for crítica
            return knowledge_data
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao decodificar o JSON em {self.file_path}: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Erro inesperado ao carregar {self.file_path}: {e}", exc_info=True)
            return None

    def _build_procedure_indexes(self):
        """Constrói os índices para busca rápida de procedimentos e variações."""
        procedures = self.data.get("procedures", [])
        if not isinstance(procedures, list):
            logger.warning("Chave 'procedures' não encontrada ou não é uma lista no JSON.")
            return

        for p_data in procedures:
            name = p_data.get("name")
            if not name or not isinstance(name, str):
                logger.warning(f"Procedimento encontrado sem nome válido ou tipo string: {p_data}")
                continue

            name_lower = name.lower().strip()
            if not name_lower: continue # Pula nomes vazios após strip

            self._procedure_map[name_lower] = p_data

            # Extração de termos de busca (removendo parênteses antes)
            search_name_processed = re.sub(r'\s*\(.*\)\s*', '', name_lower).strip()
            # Regex aprimorado para palavras (incluindo acentuadas) com 3+ letras
            terms = [term for term in re.findall(r'\b[a-zA-ZÀ-ÿ]{3,}\b', search_name_processed)
                     if term not in ["cirurgia", "geral", "conceito", "dos", "das", "para", "com"]] # Removido stop words simples
            if terms:
                self._procedure_search_terms[name_lower] = terms

            # Mapeamentos de Variações (Hardcoded - poderia vir do JSON no futuro)
            variations = []
            if "mamoplastia de aumento" in name_lower: variations = ["prótese de silicone", "silicone", "implante mamário", "aumento de mama"]
            elif "lipoaspiração de submento" in name_lower: variations = ["papada", "lipo de papada"]
            elif "lipoaspiração / lipoescultura / lipo hd" in name_lower: variations = ["lipoaspiração", "lipoescultura", "lipo hd", "lipo"]
            elif "blefaroplastia" in name_lower: variations = ["cirurgia das pálpebras", "palpebra", "palpebras"]
            elif "rinoplastia" in name_lower: variations = ["nariz", "cirurgia de nariz", "cirurgia no nariz"]
            # Adicionar mais mapeamentos conforme necessário

            for variation in variations:
                 variation_lower = variation.lower().strip()
                 if variation_lower:
                     self._procedure_variation_map[variation_lower] = name_lower # Mapeia variação -> nome original

        logger.info(f"Índices de procedimentos construídos: {len(self._procedure_map)} mapeados, {len(self._procedure_search_terms)} com termos, {len(self._procedure_variation_map)} variações.")

    # --- Funções de Formatação (Mantidas como estavam) ---

    def _format_address(self) -> Optional[str]:
        """Formata o endereço completo da clínica."""
        if not self.data: return None
        address_info = self.data.get("clinic_info", {}).get("address", {})
        if not isinstance(address_info, dict) or not address_info: return None
        parts = []
        if address_info.get('street'): parts.append(address_info['street'])
        if address_info.get('neighborhood'): parts.append(address_info['neighborhood'])
        city_state = []
        if address_info.get('city'): city_state.append(address_info['city'])
        if address_info.get('state'): city_state.append(address_info['state'])
        if city_state: parts.append("/".join(city_state))
        if address_info.get('zip_code'): parts.append(f"CEP: {address_info['zip_code']}")
        full_address = ", ".join(filter(None, parts)) # Filtra partes vazias
        ref = address_info.get('reference_point')
        if ref: full_address += f" (Referência: {ref})"
        return full_address if full_address else None

    def _format_opening_hours(self) -> Optional[str]:
        """Formata os horários de funcionamento."""
        if not self.data: return None
        hours_info = self.data.get("clinic_info", {}).get("opening_hours", {})
        if not isinstance(hours_info, dict) or not hours_info: return None
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        day_map = {"monday": "Segunda", "tuesday": "Terça", "wednesday": "Quarta",
                   "thursday": "Quinta", "friday": "Sexta", "saturday": "Sábado", "sunday": "Domingo"}
        formatted = []
        for day in days:
            hours = hours_info.get(day)
            if hours: # Assume que 'hours' já está formatado no JSON
                formatted.append(f"{day_map.get(day, day)}: {hours}")
        notes = hours_info.get("notes")
        if notes: formatted.append(f"Observação: {notes}")
        return "\n".join(filter(None, formatted)) if formatted else None

    def _format_doctor_full_experience(self) -> Optional[str]:
        """Formata TODAS as informações de experiência/qualificação do médico."""
        if not self.data or "doctor_info" not in self.data: return None
        doctor_info = self.data.get("doctor_info", {})
        if not isinstance(doctor_info, dict): return None

        info = []
        exp_summary = doctor_info.get("experience_summary", {})
        grad = doctor_info.get("graduation", {})
        post = doctor_info.get("post_graduation", [])
        fellows = doctor_info.get("fellowships", [])
        awards = doctor_info.get("awards_info", [])
        chapters = doctor_info.get("book_chapters", [])
        bio = doctor_info.get("bio_summary", "")
        philosophy = doctor_info.get("philosophy", "")
        sociedades = ""

        # Extrai sociedades do bio se mencionado
        if isinstance(bio, str) and "membro" in bio.lower():
            parts = re.split(r'\bmembro\b', bio, flags=re.IGNORECASE)
            if len(parts) > 1:
                sociedades = "membro" + " membro".join(parts[1:])
                bio_sem_sociedades = parts[0].strip() # Pega a parte antes da primeira menção
            else:
                 bio_sem_sociedades = bio.strip()
        else:
             bio_sem_sociedades = bio.strip() if isinstance(bio, str) else ""

        info.append("**Formação e Experiência Completa do Dr. Juarez Missel:**")
        if bio_sem_sociedades: info.append(f"- **Resumo Profissional:** {bio_sem_sociedades}")
        if isinstance(exp_summary, dict) and exp_summary.get('years'): info.append(f"- **Tempo de Atuação:** {exp_summary['years']}.")
        if isinstance(grad, dict) and grad.get('university'): info.append(f"- **Graduação:** Medicina pela {grad['university']} (Ano: {grad.get('year', 'N/A')}).")
        if isinstance(post, list):
             info.append("- **Especializações:**")
             for p in post:
                 if isinstance(p, dict):
                      notes_str = f" *({p['notes']})*" if p.get('notes') else ""
                      info.append(f"  - {p.get('specialty','N/A')} ({p.get('institution', 'N/A')}, Ano: {p.get('completion_year', 'N/A')}, RQE: {p.get('rqe','N/A')}).{notes_str}")
        if isinstance(fellows, list):
            info.append("- **Fellowships (Formação Complementar):**")
            for f in fellows:
                if isinstance(f, dict): info.append(f"  - {f.get('area','N/A')} no(a) {f.get('institution', 'N/A')}.")
        if sociedades: info.append(f"- **Sociedades Médicas:** {sociedades.strip()}.")
        if isinstance(awards, list):
            info.append("- **Prêmios Selecionados:**")
            for a in awards:
                if isinstance(a, dict): info.append(f"  - {a.get('award','N/A')} pelo trabalho '{a.get('title','N/A')}'.")
        if isinstance(chapters, list):
            info.append("- **Capítulos de Livros:**")
            for ch in chapters:
                if isinstance(ch, dict): info.append(f"  - No livro '{ch.get('book_title','N/A')}': Capítulo '{ch.get('chapter_title','N/A')}' (Org: {ch.get('organization','N/A')}, Ed. {ch.get('publisher','N/A')}, {ch.get('year','N/A')}).")
        if isinstance(exp_summary, dict):
            if exp_summary.get('operated_patients_surgeries'): info.append(f"- **Volume Cirúrgico:** {exp_summary['operated_patients_surgeries']}.")
            acad_parts = []
            sci_parts = []
            if exp_summary.get('scientific_presentations'): acad_parts.append(f"{exp_summary['scientific_presentations']} apresentações científicas")
            if exp_summary.get('scientific_articles'): acad_parts.append(f"{exp_summary['scientific_articles']} artigos publicados")
            if exp_summary.get('courses_taught'): acad_parts.append(f"{exp_summary['courses_taught']} cursos ministrados")
            if acad_parts: info.append(f"- **Produção Acadêmica:** {', '.join(acad_parts)}.")
            if exp_summary.get('congress_participations'): sci_parts.append(f"{exp_summary['congress_participations']} participações em congressos")
            if exp_summary.get('training_courses_taken'): sci_parts.append(f"{exp_summary['training_courses_taken']} cursos de capacitação realizados")
            if sci_parts: info.append(f"- **Atualização Contínua:** {', '.join(sci_parts)}.")
        if philosophy: info.append(f"- **Filosofia de Trabalho:** {philosophy}")

        return "\n".join(info) if len(info) > 1 else None

    def _format_procedure_details(self, procedure_data: Dict[str, Any]) -> Optional[str]:
        """Formata TODOS os detalhes de um procedimento específico."""
        if not procedure_data or not isinstance(procedure_data, dict):
            logger.warning("_format_procedure_details: Dados do procedimento inválidos ou vazios recebidos.")
            return None

        proc_name = procedure_data.get("name")
        if not proc_name or not isinstance(proc_name, str):
            logger.warning(f"_format_procedure_details: Nome do procedimento ausente ou inválido: {procedure_data}")
            return None

        proc_desc = procedure_data.get("description")
        proc_details = procedure_data.get("details", {})
        proc_type = procedure_data.get("type")

        info = []
        info.append(f"**Detalhes sobre {proc_name} ({proc_type or 'Tipo não especificado'}):**")

        if proc_desc and isinstance(proc_desc, str):
            info.append(f"- **Descrição:** {proc_desc}")

        if isinstance(proc_details, dict):
            restricao = proc_details.get("restricao_importante")
            if restricao and isinstance(restricao, str):
                info.append(f"- **Atenção Importante:** {restricao}")

            # Formata os diferenciais e outros detalhes (exceto restrição)
            diff_details_str = self._format_details_to_string(proc_details)
            if diff_details_str:
                info.append("- **Diferenciais e Informações Adicionais:**")
                info.append(diff_details_str)
        else:
            logger.warning(f"_format_procedure_details: Chave 'details' não é um dicionário para '{proc_name}'.")

        return "\n".join(info) if len(info) > 1 else None

    def _format_details_to_string(self, details_dict: Dict[str, Any]) -> str:
        """Formata um dicionário de detalhes (diferenciais, etc.) em string multi-linha, ignorando 'restricao_importante'."""
        lines = []
        title_map = {
            "diferenciais_clinico": "Clínico/Técnica",
            "diferenciais_doutor": "Dr. Juarez Missel",
            "diferenciais_artigos": "Artigos Publicados",
            "diferenciais_capitulos": "Capítulos de Livros",
            "diferenciais_premios": "Prêmios Relacionados"
        }
        # Itera sobre as chaves mapeadas para garantir a ordem (se desejado) ou sobre o dict diretamente
        for key, value in details_dict.items():
            if key == "restricao_importante" or not value: # Pula restrição e valores vazios
                 continue

            title = title_map.get(key, key.replace("diferenciais_", "").replace("_", " ").capitalize())
            if isinstance(value, list):
                # Se for lista, adiciona título e depois cada item indentado
                lines.append(f"  - **{title}:**")
                lines.extend([f"    - {item}" for item in value if item]) # Garante que itens da lista não sejam vazios
            elif isinstance(value, str):
                # Se for string, adiciona título e valor na mesma linha
                lines.append(f"  - **{title}:** {value}")
            # Ignora outros tipos por segurança

        return "\n".join(lines)

    def _find_specific_procedure(self, query_lower: str) -> Optional[Dict[str, Any]]:
        """Tenta encontrar UM procedimento específico na query usando variações e termos."""
        best_match_proc_data = None
        highest_score = 0 # 3: Variação, 2: Termos chave, 1: Nome contido

        # 1. Busca por Variações Mapeadas (maior prioridade)
        # Usa regex para encontrar a palavra/frase exata da variação na query
        for variation, original_name_lower in self._procedure_variation_map.items():
             # Usa \b para garantir que é a palavra inteira (evita match parcial como 'lipo' em 'lipofilling')
             if re.search(r'\b' + re.escape(variation) + r'\b', query_lower):
                  proc_data = self._procedure_map.get(original_name_lower)
                  if proc_data: # Se encontrou no mapa
                       logger.debug(f"Procedimento encontrado por variação mapeada: '{variation}' -> '{original_name_lower}'")
                       # Se encontrar uma variação, assume que é a intenção principal
                       return proc_data # Retorna imediatamente

        # 2. Busca por Termos Chave (se não encontrou por variação)
        # Verifica se TODOS os termos chave de um procedimento estão na query
        for name_lower, terms in self._procedure_search_terms.items():
             if terms and all(re.search(r'\b' + re.escape(term) + r'\b', query_lower) for term in terms):
                  proc_data = self._procedure_map.get(name_lower)
                  if proc_data and highest_score < 2: # Prioriza o primeiro match completo encontrado
                       logger.debug(f"Procedimento encontrado por termos chave completos: '{name_lower}' (Termos: {terms})")
                       best_match_proc_data = proc_data
                       highest_score = 2
                       # Não retorna ainda, pode haver um match melhor por nome exato (próximo passo)

        # 3. Busca por Nome Exato (Contido, Menor Prioridade) - Fallback
        # Verifica se o nome base do procedimento (sem parênteses) está contido na query
        if highest_score < 2: # Só executa se não achou por termos chave
            # Ordena por comprimento do nome descrescente para priorizar matches mais específicos
            sorted_names = sorted(self._procedure_map.keys(), key=len, reverse=True)
            for name_lower in sorted_names:
                proc_data = self._procedure_map.get(name_lower)
                name_for_match = re.sub(r'\s*\(.*\)\s*', '', name_lower).strip()
                # Verifica se o nome base está na query como palavra completa
                if len(name_for_match) > 4 and re.search(r'\b' + re.escape(name_for_match) + r'\b', query_lower):
                     logger.debug(f"Procedimento encontrado por nome contido (fallback): '{name_for_match}' em '{name_lower}'")
                     best_match_proc_data = proc_data
                     highest_score = 1
                     break # Encontrou um fallback, para aqui

        return best_match_proc_data # Retorna o melhor que encontrou (ou None)


    def get_procedure_list(self) -> Optional[str]:
        """ Retorna a lista formatada de nomes de procedimentos principais (excluindo Conceito/Pequenas)."""
        if not self.data: logger.warning("get_procedure_list: Base de conhecimento não carregada."); return None
        all_procedures = self.data.get("procedures", [])
        if not isinstance(all_procedures, list): logger.warning("get_procedure_list: 'procedures' não é uma lista ou não existe."); return None

        procedure_names = []
        try:
            for p in all_procedures:
                 if isinstance(p, dict):
                    name = p.get("name")
                    proc_type = p.get("type")
                    # Verifica se tem nome, não é Conceito e não é Pequenas Cirurgias (case-insensitive)
                    if name and isinstance(name, str) and \
                       proc_type != "Conceito" and \
                       "pequenas cirurgias" not in name.lower():
                        procedure_names.append(name)
            procedure_names.sort() # Ordena alfabeticamente
        except Exception as e:
            logger.error(f"Erro ao processar lista de procedimentos: {e}", exc_info=True)
            return None

        if procedure_names:
            logger.debug(f"get_procedure_list: Encontrados {len(procedure_names)} procedimentos principais.")
            # Formato esperado pelo OpenAI Handler
            return "- **Principais Procedimentos Realizados:**\n" + "\n".join([f"  - {name}" for name in procedure_names])
        else:
            logger.debug("get_procedure_list: Nenhum procedimento principal encontrado para listar.")
            return None

    def get_faq_answer(self, query_lower: str) -> Optional[str]:
        """ Tenta encontrar uma resposta direta na FAQ por sobreposição de palavras significativas."""
        if not self.data: logger.warning("get_faq_answer: Base de dados não carregada."); return None
        faqs = self.data.get("faq", [])
        if not isinstance(faqs, list): logger.warning("get_faq_answer: 'faq' não é uma lista ou não existe."); return None

        best_match_answer = None
        max_overlap_score = 0.5 # Threshold mínimo de score para considerar um match

        # Usa regex para palavras com 3+ letras (incluindo acentos)
        query_words = set(re.findall(r'\b[a-zA-ZÀ-ÿ]{3,}\b', query_lower))
        if not query_words: logger.debug("get_faq_answer: Query sem palavras significativas."); return None

        for faq in faqs:
            if not isinstance(faq, dict): continue # Ignora entradas malformadas
            question = faq.get("question")
            answer = faq.get("answer")
            if not question or not answer or not isinstance(question, str): continue # Pula item inválido

            question_lower = question.lower()
            faq_words = set(re.findall(r'\b[a-zA-ZÀ-ÿ]{3,}\b', question_lower))
            if not faq_words: continue # Pula pergunta FAQ vazia

            overlap_count = len(faq_words.intersection(query_words))
            # Calcula score: overlap / tamanho da MAIOR entre query e pergunta FAQ (Jaccard Index simplificado)
            # Isso penaliza menos se a query for muito longa ou muito curta em relação à pergunta
            union_size = len(faq_words.union(query_words))
            if union_size == 0: continue # Evita divisão por zero
            match_score = overlap_count / union_size

            # Critério: pelo menos 2 palavras em comum E score acima do threshold E melhor que anterior
            if overlap_count >= 2 and match_score > max_overlap_score:
                max_overlap_score = match_score
                # Formata a resposta incluindo a pergunta original para contexto claro
                best_match_answer = f"- **Respondendo sua pergunta sobre '{question.strip()}':**\n  - {answer.strip()}"
                logger.debug(f"FAQ Match Encontrado: Score={match_score:.2f}, Overlap={overlap_count}, Pergunta='{question.strip()}'")

        if not best_match_answer: logger.debug("Nenhuma correspondência forte encontrada na FAQ.")
        return best_match_answer

    def _get_formatted_links(self) -> Optional[str]:
        """Formata os links disponíveis do médico e da clínica."""
        if not self.data: return None
        dr_links = self.data.get("doctor_info", {}).get("links", {})
        clinic_website = self.data.get("clinic_info", {}).get("website")
        if not isinstance(dr_links, dict) and not clinic_website:
             logger.debug("Nenhum link encontrado no JSON."); return None

        formatted = ["- **Links e Redes Sociais:**"]
        if clinic_website and isinstance(clinic_website, str):
            formatted.append(f"  - Site Clínica: {clinic_website}")
        if isinstance(dr_links, dict):
            if dr_links.get("instagram"): formatted.append(f"  - Instagram Dr. Juarez: {dr_links['instagram']}")
            if dr_links.get("youtube"): formatted.append(f"  - YouTube Dr. Juarez: {dr_links['youtube']}")
            if dr_links.get("linkedin"): formatted.append(f"  - LinkedIn Dr. Juarez: {dr_links['linkedin']}")
            if dr_links.get("lattes"): formatted.append(f"  - Currículo Lattes Dr. Juarez: {dr_links['lattes']}")

        return "\n".join(formatted) if len(formatted) > 1 else None

    # --- FUNÇÃO PRINCIPAL DE BUSCA ---
    # ---> MODIFICAÇÃO AQUI (Assinatura e Lógica de Contexto) <---
    def find_relevant_info(self, query: str, conversation_history: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
        """
        (v8.6) Identifica intenção (considerando contexto do histórico),
        retorna bloco de info relevante e evita RAG para saudações.
        """
        # 1. Validação inicial
        if not self.data:
            logger.warning("find_relevant_info: Base de dados não carregada.")
            return None

        # 2. Normaliza a query atual
        query_lower = query.lower().strip()

        # 3. --- LÓGICA DE CONTEXTO DO HISTÓRICO ---
        identified_topic_from_history: Optional[str] = None # Guarda o nome_lower do procedimento identificado

        # Palavras/frases que indicam que a query atual depende do contexto anterior
        contextual_keywords = ["disso", "dele", "dela", "nisso", "nele", "nesse", "deste", "desta", "isso"]
        contextual_phrases = ["nesse procedimento", "sobre isso", "e os diferenciais", "mais detalhes", "e quanto a", "como funciona", "fale mais", "detalhes sobre"]

        # Verifica se a query atual parece ser contextual (contém palavra/frase chave)
        is_contextual_query = any(keyword in query_lower.split() for keyword in contextual_keywords) or \
                              any(phrase in query_lower for phrase in contextual_phrases)

        # Se for contextual e houver histórico, tenta encontrar o tópico
        if is_contextual_query and conversation_history and len(conversation_history) >= 1:
            logger.debug(f"Query '{query}' parece contextual. Analisando histórico ({len(conversation_history)} msgs)...")
            # Procura de trás para frente no histórico
            for i in range(len(conversation_history) - 1, -1, -1):
                message = conversation_history[i]
                # Foca na última resposta do ASSISTENTE que PODE ter introduzido um tópico
                if message.get("role") == "assistant":
                    assistant_response = message.get("content", "").lower()
                    found_proc_name = None
                    # Tenta encontrar um nome de procedimento (do nosso mapa) mencionado na resposta
                    # Prioriza matches mais longos para evitar ambiguidades (ex: "lipo" vs "lipoaspiração")
                    sorted_proc_names = sorted(self._procedure_map.keys(), key=len, reverse=True)
                    for proc_name_lower in sorted_proc_names:
                        search_name_processed = re.sub(r'\s*\(.*\)\s*', '', proc_name_lower).strip()
                        # Usa \b para matching de palavra inteira
                        if len(search_name_processed) > 3 and re.search(r'\b' + re.escape(search_name_processed) + r'\b', assistant_response):
                            # HEURÍSTICA: Verifica se a pergunta DO USUÁRIO ANTERIOR a esta resposta
                            # também mencionava algo relacionado a este procedimento (nome ou variação)
                            # Isso ajuda a confirmar que o assistente estava respondendo sobre esse tópico.
                            if i > 0 and conversation_history[i-1].get("role") == "user":
                                prev_user_query = conversation_history[i-1].get("content", "").lower()
                                if re.search(r'\b' + re.escape(search_name_processed) + r'\b', prev_user_query) or \
                                   any(re.search(r'\b' + re.escape(variation) + r'\b', prev_user_query)
                                       for variation, original in self._procedure_variation_map.items() if original == proc_name_lower):
                                    found_proc_name = proc_name_lower
                                    break # Achou um provável tópico relevante

                    if found_proc_name:
                        identified_topic_from_history = found_proc_name
                        logger.info(f"Contexto Histórico: Tópico '{identified_topic_from_history}' identificado baseado na interação anterior.")
                        break # Para de procurar no histórico assim que encontra um tópico provável

        # 4. --- USA O CONTEXTO SE ENCONTRADO ---
        # Se a query é contextual E um tópico foi identificado no histórico
        if is_contextual_query and identified_topic_from_history:
            logger.debug(f"Tentando RAG com base no contexto histórico: '{identified_topic_from_history}'")
            proc_data = self._procedure_map.get(identified_topic_from_history)
            if proc_data:
                proc_details_formatted = self._format_procedure_details(proc_data)
                if proc_details_formatted:
                    logger.info(f"Info RAG encontrada (Contexto Histórico - Detalhes Procedimento '{identified_topic_from_history}', Tamanho: {len(proc_details_formatted)})")
                    # RETORNA DIRETAMENTE os detalhes do procedimento identificado no contexto
                    return proc_details_formatted
                else:
                     logger.warning(f"Tópico '{identified_topic_from_history}' encontrado no histórico, mas falha ao formatar detalhes.")
            else:
                # Isso não deveria acontecer se o nome veio do _procedure_map
                logger.error(f"Tópico '{identified_topic_from_history}' identificado no histórico, mas NÃO encontrado no _procedure_map. Verifique a lógica.")
            # Se a busca contextual falhar (ex: não conseguiu formatar), a execução continua para a lógica geral abaixo como fallback

        # 5. --- TRATAMENTO DE SAUDAÇÕES / RESPOSTAS SIMPLES (APÓS contexto, ANTES de intenção geral) ---
        greetings = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "tudo bem", "tudo bom", "tudo certo", "ok", "obrigado", "obrigada", "grato", "grata", "valeu", "de nada", "igualmente"]
        greetings.extend(["td bem", "td bom", "obg", "vlw"]) # Abreviações comuns
        # Remove pontuação e verifica se a query é apenas uma dessas saudações/respostas
        simple_query = re.sub(r'[^\w\s]', '', query_lower).strip() # Remove pontuação
        if simple_query in greetings:
            logger.debug(f"Query '{query}' identificada como saudação/resposta simples. Nenhum RAG necessário.")
            # RETORNA NONE para que a OpenAI lide com a conversa naturalmente
            return None

        # 6. --- LÓGICA DE DETECÇÃO DE INTENÇÃO PRINCIPAL (Fallback se contexto não resolveu) ---
        logger.debug(f"Analisando intenção principal para query (sem contexto ou contexto falhou): '{query}'")
        knowledge_blocks: List[str] = []
        intent_found = False
        is_cost_query = any(cost_kw in query_lower for cost_kw in ["quanto custa", "valor", "preço", "custo", "média de valor", "orcamento", "orçamento"])

        # Palavras-chave (revisadas para clareza)
        exp_keywords = ["experiente", "experiência", "tempo", "anos", "formação", "formou", "currículo", "graduação", "pós-graduação", "especialização", "especialidade", "qualificação", "qualificado", "prêmio", "premios", "livro", "capítulo", "publicou", "sociedade", "membro", "fellowship", "diferenciais", "histórico", "background", "capacitação", "congresso", "científico", "artigo", "pesquisa", "palestra", "destaque", "produção acadêmica"]
        quantity_keywords = ["quantos", "quantas", "número de", "volume de"]
        surgery_proc_keywords = ["cirurgias", "procedimentos", "operações"]
        generic_dr_keywords = ["detalhes sobre ele", "tudo sobre ele", "mais sobre ele", "sobre o dr", "sobre ele", "dele"]
        doctor_keywords = ["médico", "dr.", "doutor", "juarez", "missel", "ele"] # "ele" pode ser ambíguo, mas mantido
        procedure_keywords = ["procedimento", "cirurgia", "operação", "técnica", "tratamento", "botox", "preenchimento"] + list(self._procedure_variation_map.keys()) # Inclui variações
        list_keywords = ["procedimentos", "cirurgias", "lista", "quais", "tipos", "serviços", "opções"]
        action_keywords = ["faz", "realiza", "oferece", "tem", "disponíveis"]
        link_keywords = ["instagram", "insta", "youtube", "linkedin", "lattes", "site", "link", "rede social", "redes sociais", "perfil", "página"]
        insurance_keywords = ["convênio", "convenio", "plano de saúde", "plano medico", "seguro saude", "aceita"] + KNOWN_INSURANCE_NAMES
        location_keywords = ["endereço", "onde fica", "localização", "rua", "opera", "hospital", "consultório"]
        time_keywords = ["horário", "funcionamento", "aberto", "atende", "que horas", "horarios", "agenda"]
        consult_cost_keywords = ["valor", "preço", "custo", "quanto é", "quanto custa"] # Repetido, mas ok
        payment_keywords = ["pagamento", "pagar", "forma de pagamento", "parcela", "financia"]


        # DETECÇÃO PRIORIZADA:

        # 1. Experiência / Detalhes Gerais / Quantidade do Médico
        is_exp_by_keyword = any(ek in query_lower for ek in exp_keywords) and any(dk in query_lower for dk in doctor_keywords)
        is_generic_dr_req = any(gdk in query_lower for gdk in generic_dr_keywords) and not any(pk in query_lower for pk in procedure_keywords + list_keywords)
        is_quantity_req = any(qk in query_lower for qk in quantity_keywords) and any(spk in query_lower for spk in surgery_proc_keywords) and any(dk in query_lower for dk in doctor_keywords)
        is_time_as_plastic_req = (("quanto tempo" in query_lower or "desde quando" in query_lower) and \
                                 any(pk in query_lower for pk in ["plástico", "plastico", "especialista", "cirurgião plástico"])) or \
                                 ("formou em plástica" in query_lower) # Adiciona caso específico

        # Condição para buscar experiência: SE (é uma das flags acima) E NÃO é sobre custo
        if (is_exp_by_keyword or is_generic_dr_req or is_quantity_req or is_time_as_plastic_req) and not is_cost_query:
            # Verifica se a pergunta TAMBÉM menciona um procedimento específico (para evitar RAG de experiência se a intenção for o procedimento)
            mentions_specific_procedure = self._find_specific_procedure(query_lower) is not None

            # SÓ busca experiência se NÃO mencionar procedimento específico OU se a pergunta for explicitamente genérica/quantitativa
            if not mentions_specific_procedure or is_generic_dr_req or is_quantity_req or is_time_as_plastic_req:
                logger.debug("Intenção Principal Detectada: Experiência/Detalhes/Qtd/Tempo Específico do Doutor.")
                experience_info = self._format_doctor_full_experience()
                if experience_info:
                    knowledge_blocks.append(experience_info)
                    intent_found = True
            else:
                logger.debug("Query menciona experiência E procedimento específico. Priorizando busca do procedimento.")
                # Deixa a lógica de procedimento (abaixo) tratar


        # 2. Detalhes de Procedimento Específico (Se NENHUMA intenção acima E NÃO custo)
        if not intent_found and not is_cost_query:
            # Tenta encontrar um procedimento específico na query atual
            proc_data = self._find_specific_procedure(query_lower)
            if proc_data:
                proc_name = proc_data.get('name', 'N/A')
                logger.debug(f"Intenção Principal Detectada: Detalhes do Procedimento '{proc_name}'")
                proc_details_formatted = self._format_procedure_details(proc_data)
                if proc_details_formatted:
                    knowledge_blocks.append(proc_details_formatted)
                    intent_found = True
                else: # Mesmo se falhar formatação, considera intenção encontrada para não cair em lista
                    intent_found = True
                    logger.warning(f"Intenção de procedimento '{proc_name}' detectada, mas falha ao formatar detalhes.")


        # 3. Listar Procedimentos (Se NADA antes e NÃO custo)
        # Verifica se pede lista de procedimentos/cirurgias/serviços que ele faz/realiza/oferece
        is_list_request = any(lk in query_lower for lk in list_keywords) and any(ak in query_lower for ak in action_keywords)

        if not intent_found and is_list_request and not is_cost_query:
            logger.debug("Intenção Principal Detectada: Listar Procedimentos")
            proc_list = self.get_procedure_list()
            if proc_list:
                knowledge_blocks.append(proc_list)
                intent_found = True

        # 7. --- BUSCA COMPLEMENTAR ESPECÍFICA (SOMENTE SE NENHUMA INTENÇÃO PRINCIPAL ATENDIDA) ---
        if not intent_found:
            logger.debug("Nenhuma intenção principal encontrada ou RAG já tratado pelo contexto. Verificando buscas complementares...")
            clinic_info = self.data.get("clinic_info", {})
            consultation_info = self.data.get("consultation_info", {})
            payment_methods = self.data.get("payment_methods", []) # Espera lista de strings
            insurance_info = self.data.get("accepted_insurance", []) # Espera lista de strings ou string única
            doctor_info = self.data.get("doctor_info", {}) # Para links

            # Ordem de verificação complementar:
            # Custo Consulta -> Pagamento Consulta -> Convênio -> Links -> Localização -> Horários -> Contato

            # Custo Consulta
            if any(cck in query_lower for cck in consult_cost_keywords) and "consulta" in query_lower:
                value = consultation_info.get("value")
                if value:
                     info = f"- **Valor da Consulta Inicial:** {value}"
                     logger.info(f"Info RAG encontrada (Complementar-Valor Consulta, Tamanho: {len(info)})")
                     knowledge_blocks.append(info) # Adiciona ao bloco

            # Pagamento Consulta
            elif any(pay_kw in query_lower for pay_kw in payment_keywords) and "consulta" in query_lower:
                 if payment_methods and isinstance(payment_methods, list):
                      info = f"- **Formas de Pagamento Aceitas (Consulta):** {', '.join(payment_methods)}"
                      logger.info(f"Info RAG encontrada (Complementar-Pagamento Consulta, Tamanho: {len(info)})")
                      knowledge_blocks.append(info)

            # Convênios
            elif any(ins_kw in query_lower for ins_kw in insurance_keywords):
                 # Pega a info de convênio (pode ser lista ou string)
                 insurance_text = None
                 if isinstance(insurance_info, list) and insurance_info:
                     insurance_text = insurance_info[0] # Assume a primeira entrada se for lista
                 elif isinstance(insurance_info, str):
                     insurance_text = insurance_info
                 if insurance_text:
                     info = f"- **Convênios Médicos:** {insurance_text}"
                     logger.info(f"Info RAG encontrada (Complementar-Convênio, Tamanho: {len(info)})")
                     knowledge_blocks.append(info)

            # Links
            elif any(link_kw in query_lower for link_kw in link_keywords):
                 links_info_formatted = self._get_formatted_links()
                 if links_info_formatted:
                      logger.info(f"Info RAG encontrada (Complementar-Links, Tamanho: {len(links_info_formatted)})")
                      knowledge_blocks.append(links_info_formatted)

            # Localização (Endereço ou Local Cirurgia)
            elif any(loc_kw in query_lower for loc_kw in location_keywords):
                 location_info_parts = []
                 # Verifica se pergunta ONDE opera/faz cirurgia
                 surgery_loc_keywords = ["opera", "cirurgia", "procedimento"]
                 where_keywords = ["onde", "local", "hospital"]
                 if any(slk in query_lower for slk in surgery_loc_keywords) and any(wk in query_lower for wk in where_keywords):
                     loc = clinic_info.get("surgery_location")
                     if loc: location_info_parts.append(f"- **Local das Cirurgias:** As cirurgias são realizadas no {loc}.")

                 # Verifica se pergunta endereço da clínica/consultório
                 address_keywords = ["endereço", "onde fica", "localização", "rua", "consultório", "clínica"]
                 if any(ak in query_lower for ak in address_keywords):
                     address = self._format_address()
                     if address: location_info_parts.append(f"- **Endereço da Clínica:** {address}")

                 if location_info_parts:
                      # Usa dict.fromkeys para remover duplicatas se ambas perguntas foram feitas
                      final_loc_info = "\n".join(dict.fromkeys(location_info_parts))
                      logger.info(f"Info RAG encontrada (Complementar-Localização, Tamanho: {len(final_loc_info)})")
                      knowledge_blocks.append(final_loc_info)

            # Horários
            elif any(time_kw in query_lower for time_kw in time_keywords):
                 hours = self._format_opening_hours()
                 if hours:
                     info = f"- **Horários de Funcionamento da Clínica:**\n{hours}"
                     logger.info(f"Info RAG encontrada (Complementar-Horários, Tamanho: {len(info)})")
                     knowledge_blocks.append(info)

            # Contato (Telefone/WhatsApp)
            elif any(con_kw in query_lower for con_kw in ["telefone", "contato", "whatsapp", "fone", "liga", "numero", "número"]):
                 contact_info_parts = []
                 phones = clinic_info.get("phone_number")
                 wp_raw = clinic_info.get("whatsapp_number", "")
                 # Extrai apenas o número do formato whatsapp:+55...
                 wp_number = wp_raw.split(':')[-1] if wp_raw.startswith("whatsapp:") else wp_raw

                 if phones: contact_info_parts.append(f"- **Telefones:** {phones}")
                 if wp_number: contact_info_parts.append(f"- **WhatsApp Principal (Contato):** {wp_number}")

                 if contact_info_parts:
                      final_contact_info = "\n".join(dict.fromkeys(contact_info_parts))
                      logger.info(f"Info RAG encontrada (Complementar-Contato, Tamanho: {len(final_contact_info)})")
                      knowledge_blocks.append(final_contact_info)

        # 8. --- FALLBACK PARA FAQ (Se NADA foi encontrado até agora) ---
        if not knowledge_blocks: # Verifica se blocos está vazio após todas as buscas
             logger.debug("Nenhuma informação específica (principal ou complementar) encontrada. Tentando FAQ.")
             faq_answer = self.get_faq_answer(query_lower) # Passa query_lower
             if faq_answer:
                  logger.info(f"Info RAG encontrada (FAQ, Tamanho: {len(faq_answer)})")
                  knowledge_blocks.append(faq_answer) # Adiciona resposta da FAQ

        # 9. --- FINALIZAÇÃO ---
        if knowledge_blocks:
             # Junta todos os blocos encontrados, remove vazios e duplicatas (improvável, mas seguro)
             final_knowledge = "\n\n".join(filter(None, dict.fromkeys(knowledge_blocks)))
             if final_knowledge:
                 logger.info(f"Bloco de conhecimento final RAG montado (Tamanho: {len(final_knowledge)})")
                 return final_knowledge
             else:
                 # Caso onde blocos foram adicionados mas resultaram em string vazia (raro)
                 logger.debug(f"Blocos de conhecimento encontrados, mas resultado final vazio para: '{query}'")
                 return None
        else:
             # Se NENHUM bloco foi adicionado em nenhuma etapa (principal, complementar, faq)
             logger.debug(f"Nenhuma informação relevante (principal, complementar ou FAQ) encontrada para: '{query}'")
             return None

# --- Fim da Classe KnowledgeHandler ---
    def get_scheduling_rules(self) -> Dict[str, Any]:
        """
        Retorna as regras padrão de agendamento usadas para buscar horários disponíveis.
        """
        return {
            "duration_minutes": 45,
            "preferred_days": [0, 1],  # Segunda-feira (0) e Terça-feira (1)
            "start_hour": 14,
            "end_hour": 18
        }