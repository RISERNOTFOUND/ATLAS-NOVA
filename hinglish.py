"""Hinglish -> Devanagari for NOVA's TTS.

People type Hindi in English letters ("mai aise baat kar rha hu"). Piper's Hindi voice only reads Devanagari,
so Hindi words are rewritten as Devanagari ("मैं ऐसे बात कर रहा हूँ") and English words are left alone
(they go to the English voice).
"""
import re

# Irregular spellings that the phonetic rules can't guess.
OVERRIDES = {
    "mai": "मैं", "main": "मैं", "mein": "में", "me": "में", "hu": "हूँ", "hun": "हूँ", "hoon": "हूँ",
    "hai": "है", "h": "है", "hain": "हैं", "hein": "हैं", "ho": "हो", "tha": "था", "thi": "थी", "the": "थे",
    "rha": "रहा", "rhi": "रही", "rhe": "रहे", "raha": "रहा", "rahi": "रही", "rahe": "रहे",
    "nahi": "नहीं", "nahin": "नहीं", "nhi": "नहीं", "nai": "नहीं", "na": "ना", "haan": "हाँ", "han": "हाँ", "ha": "हाँ",
    "kya": "क्या", "kyu": "क्यों", "kyun": "क्यों", "kyon": "क्यों", "kahan": "कहाँ", "kaun": "कौन", "kon": "कौन",
    "kuch": "कुछ", "kuchh": "कुछ", "koi": "कोई", "kisi": "किसी", "kisko": "किसको", "kehna": "कहना", "kehta": "कहता",
    "kehti": "कहती", "kehte": "कहते", "keh": "कह", "kaha": "कहा", "kahi": "कहीं",
    "accha": "अच्छा", "acha": "अच्छा", "achha": "अच्छा", "achhi": "अच्छी", "achchi": "अच्छी", "acchi": "अच्छी", "ache": "अच्छे",
    "theek": "ठीक", "thik": "ठीक", "bahut": "बहुत", "bohot": "बहुत", "bahot": "बहुत", "bhai": "भाई", "bhaiya": "भैया",
    "tumhe": "तुम्हें", "tumhein": "तुम्हें", "humein": "हमें", "hume": "हमें", "unhe": "उन्हें", "inhe": "इन्हें",
    "wo": "वो", "woh": "वो", "vo": "वो", "ye": "ये", "yeh": "ये", "yahan": "यहाँ", "wahan": "वहाँ", "vahan": "वहाँ",
    "toh": "तो", "to": "तो", "ki": "की", "ke": "के", "ko": "को", "ka": "का", "ne": "ने", "se": "से", "pe": "पे",
    "par": "पर", "liye": "लिए", "liya": "लिया", "diya": "दिया", "aur": "और", "ya": "या", "hi": "ही", "bhi": "भी",
    "chahiye": "चाहिए", "chahta": "चाहता", "chahti": "चाहती", "chahte": "चाहते", "pyar": "प्यार", "pyaar": "प्यार",
    "zindagi": "ज़िंदगी", "jindagi": "ज़िंदगी", "dost": "दोस्त", "zaroor": "ज़रूर", "jaroor": "ज़रूर", "zyada": "ज़्यादा",
    "jyada": "ज़्यादा", "shukriya": "शुक्रिया", "dhanyavaad": "धन्यवाद", "namaste": "नमस्ते", "kal": "कल",
    "aaj": "आज", "abhi": "अभी", "mujhe": "मुझे", "mujhko": "मुझको", "tujhe": "तुझे", "tu": "तू", "tum": "तुम",
    "sunao": "सुनाओ", "batao": "बताओ", "aao": "आओ", "khao": "खाओ", "jao": "जाओ", "lao": "लाओ", "dikhao": "दिखाओ",
    "is": "इस", "us": "उस", "do": "दो", "so": "सो", "per": "पर", "he": "हे", "sun": "सुन", "mat": "मत", "tab": "तब",
    "pal": "पल", "bar": "बार", "man": "मन", "ban": "बन", "dam": "दम", "sab": "सब", "sach": "सच", "jhoot": "झूठ",
    "achi": "अच्छी", "achii": "अच्छी", "tumhara": "तुम्हारा", "tumhari": "तुम्हारी", "tumhare": "तुम्हारे",
    "hamara": "हमारा", "hamari": "हमारी", "hamare": "हमारे", "humara": "हमारा", "humari": "हमारी", "humare": "हमारे",
    "aunga": "आऊँगा", "aungi": "आऊँगी", "jaunga": "जाऊँगा", "jaungi": "जाऊँगी", "karunga": "करूँगा", "karungi": "करूँगी",
    "bolunga": "बोलूँगा", "dekhunga": "देखूँगा", "aap": "आप", "apna": "अपना", "apni": "अपनी", "apne": "अपने",
    "kaisa": "कैसा", "kaisi": "कैसी", "rakho": "रखो", "rakha": "रखा", "pilao": "पिलाओ", "khilao": "खिलाओ",
    "yaar": "यार", "mast": "मस्त", "mazaa": "मज़ा", "maza": "मज़ा", "ek": "एक", "teen": "तीन", "char": "चार", "paanch": "पाँच",
    "ghar": "घर", "paani": "पानी", "pani": "पानी", "khaana": "खाना", "khana": "खाना", "chai": "चाय", "gaana": "गाना",
    "gaane": "गाने", "awaaz": "आवाज़", "awaz": "आवाज़", "log": "लोग", "baat": "बात", "baatein": "बातें", "kaam": "काम",
}

# Hindi words (Latin spelling) that are safe to convert anywhere. Anything in OVERRIDES counts too.
HINDI_WORDS = set("""
aap aapka aapki aapke apna apni apne hum humara hamara hamari tera tere teri mera mere meri uska uski uske unka unki
tumhara tumhari tumhare iska iski iske yahaan wahaan abhi tab jab phir fir lekin magar isliye kyunki jaise waise aise
jaisa vaisa aisa kaisa kaise kaisi kitna kitni kitne kab kabhi hamesha sirf bas bilkul thoda zyada kam bada badi bade
chhota chhoti naya nayi purana purani sundar pyara pyari sahi galat jaldi dheere dheeray accha
kar karna karo karta karti karte kiya kare karenge karunga karungi kiye kari
ja jana jao jata jati jate gaya gayi gaye jaunga jaungi jayega jayegi
aa aana aata aati aate aaya aayi aaye aayega
de dena dete deta deti de
le lena lo leta leti lete liya
bol bolna bolo bolta bolti bolte bola boli
dekh dekho dekhna dekha dekhta dekhti dekhte dikha
sun suno sunna suna sunta sunti
soch sochna socha samajh samjha samjhe samajhna pata maloom
hona hota hoti hote hua hui hue hoga hogi honge hoye
raat din subah shaam dopahar log banda bande ladka ladki insaan duniya
dil pyaar mohabbat ishq yaad bhool bhoolna
ruk ruko chal chalo chalna chalte chalta baith baitho uth utho khel khelna kha khana khilao pee pio pi
mil milna milte mila bata batana bhej bhejo laga lagta lagti lagte wapas andar bahar upar niche saath saathi beech
pehle baad kitaab kitab school kaafi kaafi bhaag naam kaha kahe kehna sabko sabhi hamesha
bhai behen behan dada dadi nana nani mummy papa chacha mama
mujhe tujhe usse isse unse inse mujhse tumse hamse
accha achha thik theek haan nahi bilkul zaroor
bohot bahut bahot khoob mazedaar zabardast badhiya kamaal dhamaal
yaar dost bhaiya didi jaan jaanu babu
kyaa kyu kyun kyon kaun kahan kidhar udhar idhar
chahiye chahta chahti chahte milega milegi
paise paisa rupaye rupay
kabhi kabhi bilkul bekar bekaar
shukriya dhanyavaad maaf maafi sorry_ nahi
pyaara pyaari dilwala khubsurat khoobsurat
subah shaam kal parso
wala wali wale
tumko hamko usko isko inko unko sabko
ladai jhagda shaant
gussa khush udaas dukhi hasna hasi roya rona
aankhein aankh haath pair sar dimag
mausam barish dhoop garmi sardi
bhukh pyaas neend
dukaan bazaar sadak gaadi
""".split())
HINDI_WORDS.discard("sorry_")
HINDI_WORDS |= set(OVERRIDES)

# Words that are ALSO common English: Hindi only if a neighbouring word is clearly Hindi.
AMBIGUOUS = {"me", "main", "to", "the", "is", "do", "so", "us", "he", "hi", "na", "per", "pe", "h", "ha",
              "sun", "mat", "tab", "ban", "pal", "bar", "man", "dam", "ya", "kar", "aa", "de", "le", "pi", "kha", "kam", "bas", "par", "ho", "han", "tu"}
DEFINITE = HINDI_WORDS - AMBIGUOUS

# Common English words kept English even inside Hindi sentences ("kya plan hai").
ENGLISH_COMMON = set("""
and but then than that this these those there here where when why how what who which while for from with without into
about over under again more most some any all each every both few many much such only own same too very can will just
should now also not yes okay hello hey bye please thanks thank good bad best nice great cool awesome love like want need
know think see look make get got give take come going went say said tell call let put use work play game gamer phone
mobile movie song music video message chat voice server discord bot channel link time day night morning evening today
tomorrow yesterday week month year people friend friends guys bro girl boy plan party mood vibe vibes status online
offline stream live level win lose team squad match rank name pic photo selfie post story reel insta instagram youtube
whatsapp snap maybe sure really actually basically literally honestly seriously wait stop start end open close free
fast slow big small old new hot cold happy sad angry tired bored hungry busy ready late early always never sometimes
already still even ever another other one two three four five six seven eight nine ten you she it we they my your his
her its our their are was were been being does did have has had chill relax lol bruh chilling vibing sorry
""".split())
ENGLISH_SUFFIXES = ("ing", "tion", "ed", "ly", "ment", "ness")

# ---- phonetic fallback for words that aren't in OVERRIDES
MULTI = [("cch", "च्छ"), ("chh", "छ"), ("ksh", "क्ष"), ("shr", "श्र"), ("kh", "ख"), ("gh", "घ"), ("ch", "च"), ("jh", "झ"),
         ("th", "थ"), ("dh", "ध"), ("ph", "फ"), ("bh", "भ"), ("sh", "श"), ("gy", "ज्ञ")]
SINGLE = {"k": "क", "g": "ग", "c": "क", "j": "ज", "t": "त", "d": "द", "n": "न", "p": "प", "f": "फ", "b": "ब", "m": "म",
          "y": "य", "r": "र", "l": "ल", "v": "व", "w": "व", "s": "स", "h": "ह", "z": "ज़", "q": "क", "x": "क्स"}
VOWELS = [("aa", "ा", "आ"), ("ai", "ै", "ऐ"), ("au", "ौ", "औ"), ("ao", "ाओ", "आओ"), ("ee", "ी", "ई"), ("ii", "ी", "ई"),
          ("oo", "ू", "ऊ"), ("uu", "ू", "ऊ"), ("a", "", "अ"), ("i", "ि", "इ"), ("u", "ु", "उ"), ("e", "े", "ए"), ("o", "ो", "ओ")]
CLUSTER_SECOND = {"य", "र", "व"}
CLUSTER_PAIRS = {("स", "त"), ("स", "क"), ("स", "प"), ("स", "म"), ("स", "न"), ("क", "त"), ("प", "त"), ("फ", "त"),
                 ("म", "ह"), ("न", "ह"), ("ल", "ह"), ("श", "र")}
NO_ANUSVARA_BEFORE = {"य", "र", "ल", "व", "ह", "न", "म"}
HALANT = "्"


def roman_to_devanagari(word):
    toks, i = [], 0
    while i < len(word):
        for chunk, dev in MULTI:
            if word.startswith(chunk, i):
                toks.append(("C", dev)); i += len(chunk); break
        else:
            for roman, matra, indep in VOWELS:
                if word.startswith(roman, i):
                    toks.append(("V", (matra, indep))); i += len(roman); break
            else:
                ch = word[i]
                toks.append(("C", SINGLE[ch]) if ch in SINGLE else ("O", ch)); i += 1
    out, last = [], len(toks) - 1
    for idx, (kind, val) in enumerate(toks):
        prev = toks[idx - 1] if idx else None
        nxt = toks[idx + 1] if idx < last else None
        if kind == "C":
            if val == "न" and prev and prev[0] == "V" and nxt and nxt[0] == "C" and nxt[1] not in NO_ANUSVARA_BEFORE:
                out.append("ं"); continue
            out.append(val)
            if nxt and nxt[0] == "C" and (nxt[1] in CLUSTER_SECOND or (val, nxt[1]) in CLUSTER_PAIRS or nxt[1] == val):
                out.append(HALANT)
        elif kind == "V":
            matra, indep = val
            if prev and prev[0] == "C":
                if idx == last and matra == "":
                    out.append("ा")       # word-final 'a' is long: kya, tha, gaya
                elif idx == last and matra == "ि":
                    out.append("ी")       # word-final 'i': paani, thi
                else:
                    out.append(matra)
            else:
                out.append(indep)
        else:
            out.append(val)
    return "".join(out)


def hinglish_to_devanagari(text):
    tokens = re.findall(r"[A-Za-z']+|[^A-Za-z']+", text)
    word_idx = [i for i, t in enumerate(tokens) if t[0].isalpha() or t[0] == "'"]
    low = {i: tokens[i].lower().replace("'", "") for i in word_idx}
    hindi = {i: low[i] in DEFINITE for i in word_idx}
    for _ in range(2):  # ambiguous words follow their neighbours (chains resolve in the second pass)
        for pos, i in enumerate(word_idx):
            if low[i] in AMBIGUOUS and not hindi[i]:
                neighbours = [word_idx[p] for p in (pos - 1, pos + 1) if 0 <= p < len(word_idx)]
                if any(hindi[n] for n in neighbours):
                    hindi[i] = True
    # In Hindi-dominant sentences, unknown words are most likely Hindi too: convert them phonetically.
    sentence_of, sent = {}, 0
    for i, tok in enumerate(tokens):
        if i in low:
            sentence_of[i] = sent
        elif re.search(r"[.!?\n]", tok):
            sent += 1
    by_sentence = {}
    for i in word_idx:
        by_sentence.setdefault(sentence_of[i], []).append(i)
    for members in by_sentence.values():
        if sum(hindi[i] for i in members) / len(members) >= 0.4:
            for i in members:
                w = low[i]
                if not hindi[i] and len(w) > 2 and w not in ENGLISH_COMMON and not w.endswith(ENGLISH_SUFFIXES):
                    hindi[i] = True
    out = []
    for i, tok in enumerate(tokens):
        if hindi.get(i):
            out.append(OVERRIDES.get(low[i]) or roman_to_devanagari(low[i]))
        else:
            out.append(tok)
    return "".join(out)
