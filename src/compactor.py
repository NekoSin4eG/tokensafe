"""
Semantic Compactor: Converts verbose natural language prompts (English, Spanish,
German, French, Portuguese, Italian, Chinese, Japanese, Russian) into ultra-dense
Token-Optimized Communication Protocol (TOCP) S-Expressions.
Supports 3-Tier Routing: Pass-through -> Multilingual Regex Compactor -> Universal Distiller.
"""

import os
import json
import re
from typing import Dict, List, Optional, Any
from src.tokenizer import count_tokens
from src.distiller import UniversalDistiller
from src.secret_redactor import SecretRedactor
from src.license_manager import LicenseManager

class PromptCompactor:
    """Transforms verbose natural language requests into compact Intent IR across major world languages."""

    STOP_PHRASES = [
        # English courtesy, prepositions, fillers & directives
        r"\b(please|kindly|could you please|could you|would you mind|would you|thank you|thanks in advance|thanks)\b",
        r"\b(hello|hi there|hi|hey|good morning|good evening|good afternoon)\b",
        r"\b(listen|look|basically|anyway|actually|to be honest|just)\b",
        r"\b(can you help me with|can you help me|can you|help me to|help me|i want you to|i would like you to|i need to|i want to|we need to|lets|let's)\b",
        r"\b(make sure to|make sure that|tell me how to|show me how to|what is the reason|how can i)\b",
        r"\b(in file|inside file|the function|the method|the class|file|function|method|class|project|code)\b",
        r"\b(add to it|make it|so that it)\b",
        r"\b(and|with|on|at|for|from|to|the|a|an|in|of|is|are|it|its|this|that|these|those)\b",

        # Spanish (Español)
        r"\b(por\s+favor|muchas\s+gracias|gracias|te\s+agradecer[ií]a|agradezco)\b",
        r"\b(hola|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches|saludos)\b",
        r"\b(mira|oye|b[aá]sicamente|en\s+realidad|de\s+hecho|a\s+ver)\b",
        r"\b(puedes\s+ayudarme|puedes|podr[ií]as|ay[uú]dame|necesito\s+que|quiero\s+que|vamos\s+a)\b",
        r"\b(aseg[uú]rate\s+de|dime\s+c[oó]mo|mu[eé]strame|cu[aá]l\s+es\s+el\s+problema|c[oó]mo\s+puedo)\b",
        r"\b(en\s+el\s+archivo|del\s+archivo|el\s+archivo|archivo|la\s+funci[oó]n|funci[oó]n|el\s+m[eé]todo|m[eé]todo|la\s+clase|clase|el\s+c[oó]digo|c[oó]digo|proyecto)\b",
        r"\b(para\s+que|de\s+modo\s+que|y|con|en|de|a|el|la|los|las|un|una|unos|unas|este|esta|esto|es|son|por)\b",

        # German (Deutsch)
        r"\b(bitte|vielen\s+dank|danke\s+im\s+voraus|danke\s+schön|danke)\b",
        r"\b(hallo|guten\s+tag|guten\s+morgen|guten\s+abend|grüß\s+gott)\b",
        r"\b(schau\s+mal|eigentlich|im\s+grunde|übrigens|quasi)\b",
        r"\b(kannst\s+du\s+mir\s+helfen|kannst\s+du|könntest\s+du|hilf\s+mir|ich\s+brauche|ich\s+möchte|wir\s+müssen|lass\s+uns)\b",
        r"\b(stelle\s+sicher|sag\s+mir|zeige\s+mir|was\s+ist\s+der\s+grund|wie\s+kann\s+ich)\b",
        r"\b(in\s+der\s+datei|aus\s+der\s+datei|die\s+datei|datei|die\s+funktion|funktion|die\s+methode|methode|die\s+klasse|klasse|der\s+code|code|projekt)\b",
        r"\b(damit|sodass|und|mit|auf|für|von|zu|der|die|das|den|dem|ein|eine|dies|diese|ist|sind)\b",

        # French (Français)
        r"\b(s'il\s+vous\s+pla[iî]t|s'il\s+te\s+pla[iî]t|svp|stp|merci\s+d'avance|merci\s+beaucoup|merci)\b",
        r"\b(bonjour|bonsoir|salut|coucou|bienvenue)\b",
        r"\b([eé]coute|regarde|en\s+fait|fondamentalement|au\s+fait|bref)\b",
        r"\b(peux-tu\s+m'aider|peux-tu|pourrais-tu|aide-moi|j'ai\s+besoin\s+de|je\s+veux\s+que|il\s+faut\s+que)\b",
        r"\b(assure-toi\s+de|dis-moi\s+comment|montre-moi|quelle\s+est\s+la\s+raison|comment\s+faire)\b",
        r"\b(dans\s+le\s+fichier|du\s+fichier|le\s+fichier|fichier|la\s+fonction|fonction|la\s+m[eé]thode|m[eé]thode|la\s+classe|classe|le\s+code|code|projet)\b",
        r"\b(pour\s+que|afin\s+que|et|avec|sur|dans|pour|de|le|la|les|un|une|des|ce|cette|ces|est|sont)\b",

        # Portuguese (Português)
        r"\b(por\s+favor|muito\s+obrigado|obrigado|obrigada|valeu|grato)\b",
        r"\b(ol[aá]|bom\s+dia|boa\s+tarde|boa\s+noite|fala|e\s+a[ií])\b",
        r"\b(olha|veja|basicamente|na\s+verdade|ent[aã]o)\b",
        r"\b(voc[eê]\s+pode|poderia|ajude-me|me\s+ajuda|preciso\s+que|quero\s+que|vamos)\b",
        r"\b(certifique-se\s+de|me\s+diga\s+como|me\s+mostre|qual\s+[eé]\s+o\s+motivo|como\s+posso)\b",
        r"\b(no\s+arquivo|do\s+arquivo|o\s+arquivo|arquivo|a\s+funç[aã]o|funç[aã]o|o\s+m[eé]todo|m[eé]todo|a\s+classe|classe|o\s+c[oó]digo|c[oó]digo|projeto)\b",
        r"\b(para\s+que|de\s+modo\s+que|e|com|em|de|a|o|a|os|as|um|uma|este|esta|isso|isto|[eé]|s[aã]o)\b",

        # Italian (Italiano)
        r"\b(per\s+favore|per\s+cortesia|grazie\s+mille|grazie)\b",
        r"\b(ciao|buongiorno|buonasera|salve)\b",
        r"\b(puoi|potresti|aiutami|ho\s+bisogno\s+di|voglio\s+che)\b",
        r"\b(nel\s+file|dal\s+file|il\s+file|file|la\s+funzione|funzione|il\s+metodo|metodo|la\s+classe|classe|il\s+codice|codice|progetto)\b",
        r"\b(in\s+modo\s+che|affinch[eé]|e|con|su|in|di|a|il|lo|la|i|gli|le|un|uno|una|questo|questa|[eè]|sono)\b",

        # Russian (Русский)
        r"\b(пожалуйста|будь добр|будь любезен|если не трудно|заранее спасибо|спасибо|благодарю)\b",
        r"\b(привет|здравствуй|здравствуйте|добрый день|добрый вечер|доброе утро|хай|салют)\b",
        r"\b(слушай|смотри|короче|кстати|в общем|в общем-то|дело в том что)\b",
        r"\b(можешь ли ты|можешь|не мог бы ты|помоги мне|помоги пожалуйста|мне нужно|я хочу чтобы ты|я хочу|нужно чтобы|надо чтобы|надо|нужно|давай)\b",
        r"\b(подскажи как|подскажи пожалуйста|подскажи|расскажи как|сделай так чтобы|сделай пожалуйста|попробуй|попробуем)\b",
        r"\b(как думаешь|как считаешь|по твоему мнению|в чем проблема|почему|почему падает|не работает)\b",
        r"\b(в файле|из файла|внутри файла|файле|файл|функцию|функция|функции|метод|методе|метода|класс|классе|класса|код|кода|строку|строка|проект|проекта|проекте|описани\w*|пояснени\w*|информаци\w*|баз\w*\s+данных|баз[аеуоы]|бд|database)\b",
        r"\b(добавь туда|сделай её|сделай его|сделай их|чтобы было|чтобы он|чтобы она|чтобы оно|для этого|для того чтобы)\b",
        # Note: «не» (negation) is deliberately NOT stripped — removing it inverted
        # the meaning of prompts like "не удаляй файл" ("do not delete the file").
        r"\b(и|в|на|с|при|по|к|из|у|о|об|от|под|над|для|бы|ли|же|то|это|этот|эта|эти|он|она|оно|они|ему|ей|им|их|его|ее)\b",

        # Chinese (中文)
        r"(请|麻烦|谢谢|多谢|你好|您好|早上好|晚上好)",
        r"(你看|其实|基本上|你能|可以帮我|帮我|我需要|我想|我们要|让我们)",
        r"(请问|为什么|怎么做|如何|看一下)",
        r"(在文件|文件里|文件|中的|函数|方法|类|代码|项目)",
        r"(为了|以便|并且|和|与|在|从|把|将|使|让|的|了|是|这|那)",

        # Japanese (日本語)
        r"(お願いします|お願い|ありがとう|よろしく|こんにちは|おはよう|こんばんは)",
        r"(できますか|手伝って|必要があります|したい|しましょう|教えて|どうやって|なぜ)",
        r"(ファイル内|ファイル|関数|メソッド|クラス|コード|プロジェクト)",
        r"(のために|ように|そして|で|に|を|は|が|の|と|から|へ|これ|それ)",
    ]

    ACTION_PATTERNS = [
        ("TEST", re.compile(
            r"\b(test|spec|assert|coverage|unit[\s_-]?test|e2e|"
            r"probar|prueba\w*|testear\w*|"
            r"testen\w*|"
            r"tester\w*|"
            r"testar\w*|teste\w*|"
            r"collaudar\w*|"
            r"тест\w*|протестируй\w*|покрыт\w*\s+тест\w*)\b|"
            r"(测试|单测|断言|テスト|単体テスト)",
            re.I
        )),
        ("DEPLOY", re.compile(
            r"\b(deploy|release|publish|ship|"
            r"despleg\w*|public\w*|lanz\w*|"
            r"bereitstell\w*|veröffentlich\w*|releas\w*|"
            r"déploy\w*|"
            r"implant\w*|"
            r"rilasci\w*|"
            r"выкат\w*|задепло\w*|опублику\w*|релиз\w*)\b|"
            r"(部署|发布|上线|デプロイ|リリース|公開)",
            re.I
        )),
        ("BENCHMARK", re.compile(
            r"\b(benchmark|bench|profil\w*|"
            r"evaluar|medir\s+rendimiento|"
            r"profiling|leistungsmessung|"
            r"analyser\s+la\s+performance|"
            r"замер\w*|бенчмарк\w*|профайлинг\w*)\b|"
            r"(基准测试|压测|性能分析|ベンチマーク|性能測定)",
            re.I
        )),
        ("DOCS", re.compile(
            r"\b(document\w*|readme|docs?|docstring|swagger|"
            r"documentar|documento\w*|documentaci[oó]n|"
            r"dokumentier\w*|dokumentation|"
            r"documentaç[aã]o|"
            r"documentare|"
            r"документ\w*|доку\b|дока\b|документаци\w*|комментар\w*)\b|"
            r"(文档|说明文档|注释|ドキュメント|文書化|コメント)",
            re.I
        )),
        ("CLEAN", re.compile(
            r"\b(clean(?:up)?|remove|delete|prune|vacuum|"
            r"limpi\w*|elimin\w*|borr\w*|quit\w*|"
            r"bereinig\w*|lösch\w*|entfern\w*|aufräum\w*|"
            r"nettoy\w*|supprim\w*|effac\w*|"
            r"remov\w*|exclu\w*|apag\w*|"
            r"pulir\w*|"
            r"удал\w*|почист\w*|очист\w*|сотри)\b|"
            r"(清理|清除|删除|クリーンアップ|削除|クリーン)",
            re.I
        )),
        ("CONFIG", re.compile(
            r"\b(config(?:ure)?|setup|env|"
            r"configur\w*|ajust\w*|"
            r"einricht\w*|"
            r"настро\w*|конфигур\w*|сконфигурируй)\b|"
            r"(配置|设置|环境|設定|構成|セットアップ)",
            re.I
        )),
        ("UPDATE", re.compile(
            r"\b(update|upgrade|bump|"
            r"actualiz\w*|"
            r"updat\w*|"
            r"mettre\s+à\s+jour|"
            r"atualiz\w*|"
            r"aggiorn\w*|"
            r"обнов\w*|актуализируй\w*|подними\s+верси\w*)\b|"
            r"(更新|升级|アップグレード)",
            re.I
        )),
        ("OPTIMIZE", re.compile(
            r"\b(optimize|speed\s*up|perf|memory\s*leak|latency|"
            r"optimiz\w*|aceler\w*|"
            r"beschleunig\w*|"
            r"accélér\w*|"
            r"otimiz\w*|"
            r"ottimizz\w*|"
            r"ускор\w*|оптимизируй\w*|быстрее)\b|"
            r"(优化|加速|性能提升|最適化|高速化)",
            re.I
        )),
        ("REFACTOR", re.compile(
            r"\b(refactor|rewrite|restructure|"
            r"refactoriz\w*|reescrib\w*|reestructur\w*|"
            r"refaktorisier\w*|umschreib\w*|umstrukturier\w*|"
            r"refactoris\w*|réécr\w*|restructur\w*|"
            r"refator\w*|reescrev\w*|"
            r"rifattorizz\w*|"
            r"перепиш\w*|отрефактор\w*|передела\w*|модернизируй\w*)\b|"
            r"(重构|重写|改构|リファクタリング|再構築|書き直し)",
            re.I
        )),
        ("DEBUG", re.compile(
            r"\b(fix|debug|resolve|error|bug|issue|broken|failing|crash|build\s*fail\w*|"
            r"arregl\w*|correg\w*|repar\w*|depur\w*|fall\w*|"
            r"beheb\w*|reparier\w*|fehlschlag\w*|fehler\w*|korrigier\w*|"
            r"corrig\w*|répar\w*|débogu\w*|bogue\w*|"
            r"consert\w*|falha\w*|"
            r"risolv\w*|guast\w*|"
            r"почин\w*|исправ\w*|баг\w*|ошибк\w*|не\s*работает|пада\w*|падени\w*|не\s*собирается|собирается|не\s*компилируется|сборк\w*|билд\w*)\b|"
            r"(修复|修好|调试|报错|错误|崩溃|修正|デバッグ|バグ|エラー|不具合)",
            re.I
        )),
        ("CREATE", re.compile(
            r"\b(create|add|implement|build|write|new|generate|"
            r"cre\w*|añad\w*|agreg\w*|implement\w*|escrib\w*|gener\w*|"
            r"erstell\w*|hinzufüg\w*|bau\w*|"
            r"cré\w*|ajout\w*|écr\w*|"
            r"adicion\w*|escrev\w*|"
            r"creare|aggiung\w*|"
            r"созда\w*|добав\w*|напиш\w*|реализу\w*|сдела\w*)\b|"
            r"(创建|添加|实现|编写|新建|生成|作成|追加|実装|書く|新規)",
            re.I
        )),
        ("AUDIT", re.compile(
            r"\b(audit|review|check|security|analyze|verify|"
            r"auditar|revisar|verificar|analizar|"
            r"überprüf\w*|prüf\w*|analysier\w*|auditier\w*|"
            r"auditer|inspect\w*|vérifi\w*|"
            r"ispezion\w*|"
            r"провер\w*|проанализируй\w*|аудит\w*|ревью)\b|"
            r"(审查|审核|检查|分析|验证|監査|レビュー|確認|診断|解析|検証)",
            re.I
        )),
        ("RUN", re.compile(
            r"\b(run|execute|start|launch|"
            r"ejecutar|iniciar|lanzar|"
            r"ausführen|starten|"
            r"exécuter|démarrer|"
            r"rodar|"
            r"eseguire|"
            r"запуст\w*|выполн\w*|старту\w*)\b|"
            r"(运行|执行|启动|実行|起動|開始)",
            re.I
        )),
        ("EXPLAIN", re.compile(
            r"\b(explain|why|how\s+does|what\s+is|clarify|"
            r"explicar|por\s+qu[eé]|c[oó]mo\s+funciona|qu[eé]\s+es|"
            r"erklären|warum|wie\s+funktioniert|was\s+ist|"
            r"expliquer|pourquoi|comment\s+fonctionne|"
            r"spiegare|perch[eé]|come\s+funziona|"
            r"объясн\w*|почему|как\s+работает|что\s+делает|поясн\w*)\b|"
            r"(解释|为什么|如何工作|什么作用|说明|説明|なぜ|どう動く|どういうこと|解説)",
            re.I
        )),
    ]

    FILE_REGEX = re.compile(
        r'(?:'
        r'(?:[\w\-\./\\]+[\/\\])?[\w\-\.]+\.(?:ts|js|tsx|jsx|py|go|rs|java|c|cpp|h|hpp|json|yaml|yml|md|proto|toml|sql|html|css|scss|sh|bash|zsh|lock|vue|svelte|graphql|prisma)'
        r'|(?:\.env(?:\.[\w\-]+)?|\.gitignore|\.dockerignore|\.prettierrc[\w\.\-]*|\.eslintrc[\w\.\-]*|\.npmignore|\.npmrc|\.yarnrc)'
        r'|\b(?:Dockerfile(?:\.[\w\-]+)?|Makefile|Procfile|Gemfile|Vagrantfile|Containerfile)\b'
        r'|\b(?:README(?:\.md)?|LICENSE|CHANGELOG(?:\.md)?)\b'
        r')',
        re.I
    )

    TRAIT_MAPPINGS = [
        (re.compile(r"\b(async|асинхронн\w*|as[ií]ncron\w*|asynchrone|asynchron)\b|(异步|非同期)", re.I), "async"),
        (re.compile(r"\b(sync|синхронн\w*|s[ií]ncron\w*|synchrone|synchron)\b|(同步)", re.I), "sync"),
        (re.compile(r"\b(redis|редис)\b", re.I), "redis"),
        (re.compile(r"\b(cache|кэш\w*|кеш\w*|cach[eé]|speicher)\b|(缓存|キャッシュ)", re.I), "cache"),
        (re.compile(r"\b(lru)\b", re.I), "lru"),
        (re.compile(r"\b(postgres|postgresql|постгрес)\b", re.I), "postgres"),
        (re.compile(r"\b(sqlite)\b", re.I), "sqlite"),
        (re.compile(r"\b(mysql)\b", re.I), "mysql"),
        (re.compile(r"\b(prisma|призма)\b", re.I), "prisma"),
        (re.compile(r"\b(docker|докер)\b", re.I), "docker"),
        (re.compile(r"\b(jwt|токен\w*|auth|авторизац\w*|autenticaci[oó]n|authentification|token)\b|(认证|認証)", re.I), "jwt"),
        (re.compile(r"\b(oauth)\b", re.I), "oauth"),
        (re.compile(r"\b(zero_alloc|без\s+аллокаций|sin\s+asignaciones|ohne\s+allokation)\b", re.I), "zero_alloc"),
        (re.compile(r"\b(typesafe|типизац\w*|типобезопасн\w*|типами|типы|tipado|typesécurisé|typsicher)\b|(类型安全|型安全)", re.I), "typesafe"),
        (re.compile(r"\b(tailwind)\b", re.I), "tailwind"),
        (re.compile(r"\b(unit[\s_-]?test|юнит\w*|prueba\s+unitaria|test\s+unitaire)\b|(单测|単体テスト)", re.I), "unit_test"),
        (re.compile(r"\b(e2e)\b", re.I), "e2e"),
        (re.compile(r"\b(mock|мок\w*)\b", re.I), "mock"),
        (re.compile(r"\b(npm)\b", re.I), "npm"),
        (re.compile(r"\b(pypi)\b", re.I), "pypi"),
        (re.compile(r"\b(gemini|гемини)\b", re.I), "gemini"),
        (re.compile(r"\b(deepseek|дипсик)\b", re.I), "deepseek"),
        (re.compile(r"\b(swagger|openapi)\b", re.I), "swagger"),
        (re.compile(r"\b(rest|api)\b", re.I), "rest"),
        (re.compile(r"\b(graphql)\b", re.I), "graphql"),
        (re.compile(r"\b(grpc)\b", re.I), "grpc"),
        (re.compile(r"\b(websocket|ws)\b", re.I), "websocket"),
        (re.compile(r"\b(sse)\b", re.I), "sse"),
        (re.compile(r"\b(react)\b", re.I), "react"),
        (re.compile(r"\b(vite)\b", re.I), "vite"),
        (re.compile(r"\b(nextjs|next\.js)\b", re.I), "nextjs"),
        (re.compile(r"\b(git|github)\b", re.I), "git"),
        (re.compile(r"\b(ci|github_actions|action)\b", re.I), "ci"),
        (re.compile(r"\b(mcp)\b", re.I), "mcp"),
        (re.compile(r"\b(crypto|ed25519|aes|cripto|krypto|cryptographie)\b|(加密|暗号化)", re.I), "crypto"),
    ]

    SYMBOL_KEYWORDS = (
        r"(?:function|función|funcion|funktion|fonction|função|funcao|funzione|функци[яиейюь]*|"
        r"method|método|metodo|methode|méthode|метод[аеуомы]*|"
        r"class|clase|klasse|classe|класс[аеуомы]*|"
        r"handler|gestionnaire|gestore|хэндлер\w*|"
        r"endpoint|эндпоинт\w*|enrutador|"
        r"component|componente|composant|komponente|компонент\w*|"
        r"route|ruta|роут\w*|"
        r"hook|хук\w*|"
        r"service|servicio|serviço|servizio|dienst|сервис\w*|"
        r"controller|controlador|contrôleur|controleur|контроллер\w*|"
        r"variable|variável|variavel|variabile|переменн[уюаяые]+|"
        r"interface|interfaz|schnittstelle|interfaccia|интерфейс\w*|"
        r"type|tipo|typ|тип\w*|"
        r"struct|estructura|struktur|structure|struttura|структур\w*|"
        r"symbol|símbolo|simbolo|symbole|символ\w*)"
    )
    SYMBOL_REGEX_KEYWORD = re.compile(SYMBOL_KEYWORDS + r"\s+[`'\"\(]?([a-zA-Z_][a-zA-Z0-9_]*)", re.I)
    SYMBOL_CJK_KW = r"(?:函数|方法|类|接口|类型|结构体|变量|端点|组件|関数|メソッド|クラス|ハンドラー|エンドポイント|コンポーネント|変数|型|構造体)"
    SYMBOL_REGEX_CJK_PREFIX = re.compile(SYMBOL_CJK_KW + r"[\s`'\"\(]*([a-zA-Z_][a-zA-Z0-9_]*)")
    SYMBOL_REGEX_CJK_SUFFIX = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)[\s`'\"\)]*" + SYMBOL_CJK_KW)
    SYMBOL_REGEX_BACKTICK = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`")
    SYMBOL_REGEX_CALL = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\(\)")

    DEFAULT_MIN_DISTILL_TOKENS = 100

    @classmethod
    def _get_min_distill_tokens(cls) -> int:
        """Loads minimum token threshold for LLM distillation from config or returns 100."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, "data", "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    conf = json.load(f)
                    if "llm_distill_min_tokens" in conf:
                        return int(conf["llm_distill_min_tokens"])
            except Exception:
                pass
        return cls.DEFAULT_MIN_DISTILL_TOKENS

    @classmethod
    def extract_symbol(cls, text: str, target_file: str = "") -> str:
        """Extracts identifier/symbol name from text across supported languages."""
        m_sym = cls.SYMBOL_REGEX_KEYWORD.search(text)
        if m_sym and m_sym.group(1) != target_file:
            return m_sym.group(1)
        m_cjk_pre = cls.SYMBOL_REGEX_CJK_PREFIX.search(text)
        if m_cjk_pre and m_cjk_pre.group(1) != target_file:
            return m_cjk_pre.group(1)
        m_cjk_suf = cls.SYMBOL_REGEX_CJK_SUFFIX.search(text)
        if m_cjk_suf and m_cjk_suf.group(1) != target_file:
            return m_cjk_suf.group(1)
        m_backtick = cls.SYMBOL_REGEX_BACKTICK.search(text)
        if m_backtick and m_backtick.group(1) != target_file:
            return m_backtick.group(1)
        m_call = cls.SYMBOL_REGEX_CALL.search(text)
        if m_call and m_call.group(1) != target_file:
            return m_call.group(1)
        return ""

    @classmethod
    def compact(
        cls,
        raw_text: str,
        enable_deepseek: bool = True,
        min_distill_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Compresses input prompt via 3-Tier Architecture:
        - Tier 0: Pass-through for very short inputs (< 6 tokens)
        - Tier 1: Multilingual Regex Compactor (0ms, 0 external tokens) for inputs < 100 tokens
        - Tier 2: Universal Distiller (DeepSeek / Gemini / OpenAI) for long/complex prompts (>= 100 tokens)
        """
        if not raw_text or not raw_text.strip():
            return {
                "original_text": raw_text or "",
                "compact_text": raw_text or "",
                "original_tokens": 0,
                "compact_tokens": 0,
                "saved_tokens": 0,
                "reduction_pct": 0.0,
                "action": "UNKNOWN",
                "target_file": "",
                "symbol": "",
                "traits": [],
                "tier": "PASS_THROUGH"
            }

        # Step 0: Local Secret Redaction (Stripe, AWS, JWT, DB URLs, Private Keys)
        scrubbed_text, secrets_scrubbed, _ = SecretRedactor.redact_text(raw_text)
        if secrets_scrubbed > 0:
            raw_text = scrubbed_text

        original_tokens = count_tokens(raw_text)

        # Tier 0: Short commands pass through
        if original_tokens < 6:
            return {
                "original_text": raw_text,
                "compact_text": raw_text.strip(),
                "original_tokens": original_tokens,
                "compact_tokens": original_tokens,
                "saved_tokens": 0,
                "reduction_pct": 0.0,
                "action": "RAW",
                "target_file": "",
                "symbol": "",
                "traits": [],
                "tier": "PASS_THROUGH"
            }

        # Tier 2: Neural LLM Intent Distiller (Gemini / OpenAI / DeepSeek)
        # Exclusive to TokenSafe Solo, Team, and Enterprise (or active trials)
        min_distill = min_distill_tokens if min_distill_tokens is not None else cls._get_min_distill_tokens()
        if enable_deepseek and original_tokens >= min_distill:
            active_tier, _ = LicenseManager.get_active_license()
            if active_tier != "free":
                distilled = UniversalDistiller.distill(raw_text)
                if distilled:
                    dist_tokens = count_tokens(distilled)
                    if dist_tokens < original_tokens:
                        saved = original_tokens - dist_tokens
                        pct = (saved / original_tokens * 100.0)

                        # Extract action from S-Expression: e.g. "(DEBUG ..."
                        act_match = re.match(r"^\(([A-Z]+)", distilled)
                        action = act_match.group(1) if act_match else "TASK"

                        # Extract file if present
                        files = cls.FILE_REGEX.findall(distilled)
                        target_file = files[0] if files else ""

                        # Extract symbol
                        symbol = cls.extract_symbol(raw_text, target_file)

                        # Extract traits if present in distilled S-Expression: e.g. [async redis ...]
                        trait_match = re.search(r"\[(.*?)\]", distilled)
                        traits = trait_match.group(1).split() if trait_match else []

                        return {
                            "original_text": raw_text,
                            "compact_text": distilled,
                            "original_tokens": original_tokens,
                            "compact_tokens": dist_tokens,
                            "saved_tokens": saved,
                            "reduction_pct": round(pct, 1),
                            "action": action,
                            "target_file": target_file,
                            "symbol": symbol,
                            "traits": traits,
                            "tier": "DEEPSEEK_V3"
                        }

        # Tier 1: Multilingual Local Regex Compactor
        files = cls.FILE_REGEX.findall(raw_text)
        target_file = files[0] if files else ""

        action = "TASK"
        for act_name, regex in cls.ACTION_PATTERNS:
            if regex.search(raw_text):
                action = act_name
                break

        traits = []
        for regex, tag in cls.TRAIT_MAPPINGS:
            if regex.search(raw_text):
                if tag not in traits:
                    traits.append(tag)

        symbol = cls.extract_symbol(raw_text, target_file)

        delta = raw_text
        if target_file:
            delta = delta.replace(target_file, " ")
        if symbol:
            delta = re.sub(r"\b" + re.escape(symbol) + r"\b", " ", delta)
            delta = delta.replace(f"`{symbol}`", " ")

        for pattern in cls.STOP_PHRASES:
            delta = re.sub(pattern, " ", delta, flags=re.I)

        for _, regex in cls.ACTION_PATTERNS:
            delta = regex.sub(" ", delta)

        for regex, _ in cls.TRAIT_MAPPINGS:
            delta = regex.sub(" ", delta)

        delta = re.sub(r"[\.,:;\"'\(\)\[\]\{\}`\?¿¡！。，：；“”‘’「」『』、]", " ", delta)
        delta = re.sub(r"\s+", " ", delta).strip()

        parts = [action]
        if target_file:
            parts.append(target_file)
        if symbol:
            parts.append(symbol)
        if traits:
            parts.append(f"[{' '.join(traits)}]")
        if delta and len(delta) > 2:
            parts.append(f'"{delta}"')

        compact_expr = f"({' '.join(parts)})"
        compact_tokens = count_tokens(compact_expr)

        if compact_tokens > original_tokens:
            compact_expr = raw_text.strip()
            compact_tokens = original_tokens

        saved = max(0, original_tokens - compact_tokens)
        pct = (saved / original_tokens * 100.0) if original_tokens > 0 else 0.0

        return {
            "original_text": raw_text,
            "compact_text": compact_expr,
            "original_tokens": original_tokens,
            "compact_tokens": compact_tokens,
            "saved_tokens": saved,
            "reduction_pct": round(pct, 1),
            "action": action,
            "target_file": target_file,
            "symbol": symbol,
            "traits": traits,
            "tier": "REGEX"
        }
