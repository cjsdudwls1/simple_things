import os
import json
import re
import datetime
import gradio as gr
import requests
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 환경변수 읽기
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MONDAY_API_KEY = os.getenv("MONDAY_API_KEY")

if not (OPENAI_API_KEY and MONDAY_API_KEY):
    raise ValueError("환경변수 (.env 파일)에서 OPENAI_API_KEY와 MONDAY_API_KEY를 설정하세요.")

def register_schedule(schedule_data, board_id):
    """
    monday.com에 일정 등록을 위한 함수  
    schedule_data: dict, 예상 키: title, start_date, end_date, description  
    board_id: str, monday.com의 보드 ID
    """
    item_name = schedule_data.get("title", "새 일정")   
    start_date = schedule_data.get("start_date", "")
    end_date = schedule_data.get("end_date", "")
    description = schedule_data.get("description", "")
    
    # 상대 날짜 표현이 남아있을 경우 convert_relative_date 함수로 변환
    start_date = convert_relative_date(start_date)
    if end_date:
        end_date = convert_relative_date(end_date)
    
    # 등록 시 사용할 설명 텍스트 구성
    full_description = f"시작 날짜: {start_date}"
    if end_date:
        full_description += f", 종료 날짜: {end_date}"
    if description:
        full_description += f"\n설명: {description}"
    
    # monday.com에서 사용하는 컬럼 값을 JSON 문자열로 생성 (예: 'date'와 'text' 컬럼)
    column_values = json.dumps({
         "date": {"date": start_date},
         "text": full_description
    })
    
    # GraphQL 쿼리: boardId의 타입을 ID!로 선언
    query = """
    mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON!) {
      create_item(board_id: $boardId, item_name: $itemName, column_values: $columnValues) {
        id
      }
    }
    """
    variables = {
        "boardId": board_id,
        "itemName": item_name,
        "columnValues": column_values
    }
    
    headers = {
         "Authorization": MONDAY_API_KEY,
         "Content-Type": "application/json"
    }
    
    response = requests.post(
        "https://api.monday.com/v2",
        json={"query": query, "variables": variables},
        headers=headers
    )
    return response.json()

def convert_relative_date(date_str):
    """
    상대 날짜 표현(예: "내일", "모레", "다음주 월요일", "다다음주 월요일")을 실제 날짜(YYYY-MM-DD)로 변환하는 함수.
    
    - "내일", "모레"는 오늘 기준 1일, 2일 후로 계산합니다.
    - "다음주 [요일]"은 이번 주 일요일 이후, 다음 주의 해당 요일로 계산합니다.
    - "다다음주 [요일]"은 다음 주 월요일 기준으로 추가 7일 후의 해당 요일로 계산합니다.
    """
    # 이미 YYYY-MM-DD 형식이면 그대로 반환
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        pass

    today = datetime.date.today()
    date_str = date_str.strip()
    
    if date_str == "내일":
        return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    elif date_str == "모레":
        return (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    # "다다음주"가 포함된 경우 먼저 처리합니다.
    elif "다다음주" in date_str:
        weekdays = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}
        for day_name, day_index in weekdays.items():
            if day_name in date_str:
                # 이번 주 일요일 이후의 다음 주 월요일
                next_week_monday = today + datetime.timedelta(days=(6 - today.weekday() + 1))
                # 다음 주 월요일 + 7일(즉, 다다음주 월요일) 이후에 요일에 해당하는 날
                target_date = next_week_monday + datetime.timedelta(days=7+day_index)
                return target_date.strftime("%Y-%m-%d")
    elif "다음주" in date_str:
        weekdays = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}
        for day_name, day_index in weekdays.items():
            if day_name in date_str:
                next_week_monday = today + datetime.timedelta(days=(6 - today.weekday() + 1))
                target_date = next_week_monday + datetime.timedelta(days=day_index)
                return target_date.strftime("%Y-%m-%d")
    # 변환할 수 없으면 원본 문자열 반환
    return date_str

def fix_json(text):
    """
    간단한 정규표현식을 이용하여 JSON에서 따옴표가 누락된 부분을 수정하는 함수.
    모든 케이스를 보완하지는 못할 수 있으므로 프롬프트에서 올바른 JSON 출력을 유도하는 것이 중요합니다.
    """
    pattern = re.compile(r'(:\s*)([A-Za-z가-힣]+(?:\s+[A-Za-z가-힣]+)*)')
    fixed_text = pattern.sub(r'\1"\2"', text)
    return fixed_text

def safe_json_loads(text):
    """
    json.loads() 호출 시 에러가 발생하면 fix_json()을 적용한 후 재시도하는 함수.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fixed = fix_json(text)
        return json.loads(fixed)

def process_schedule_input(board_id, user_input):
    """
    사용자의 자연어 일정 입력을 OpenAI Chat Completions API를 통해 처리한 후,
    monday.com에 일정을 등록하는 함수.
    """
    if not board_id:
        return "보드 ID를 입력해주세요."
    
    # 현재 날짜를 동적으로 구하여 프롬프트에 포함
    today = datetime.date.today()
    today_str = today.strftime("%Y년 %m월 %d일")
    
    prompt = f"""
오늘은 {today_str}입니다.
다음 사용자의 입력을 바탕으로 일정 정보를 추출하여, 아래의 JSON 형식을 완전히 준수하는 순수 JSON 문자열로 결과를 출력해 주세요.
추가 텍스트나 설명은 포함하지 말고, 모든 속성과 문자열은 반드시 쌍따옴표(")로 감싸야 합니다.
예시 JSON 형식:
{{
  "title": "일정 제목",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "description": "일정 설명"
}}
입력: {user_input}
    """
    try:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "당신은 사용자 입력에서 일정 정보를 추출하는 전문가입니다."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0
        }
        response = requests.post(url, headers=headers, json=data)
        response_json = response.json()
        schedule_json_str = response_json["choices"][0]["message"]["content"].strip()
        schedule_data = safe_json_loads(schedule_json_str)
        
        # 사용자 입력에 "다다음주" 또는 "다음주"와 요일이 포함되어 있다면 직접 날짜를 재계산합니다.
        if "다다음주" in user_input:
            weekdays = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}
            for day_name, day_index in weekdays.items():
                if day_name in user_input:
                    next_week_monday = today + datetime.timedelta(days=(6 - today.weekday() + 1))
                    target_date = next_week_monday + datetime.timedelta(days=7+day_index)
                    schedule_data["start_date"] = target_date.strftime("%Y-%m-%d")
                    break
        elif "다음주" in user_input:
            weekdays = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}
            for day_name, day_index in weekdays.items():
                if day_name in user_input:
                    next_week_monday = today + datetime.timedelta(days=(6 - today.weekday() + 1))
                    target_date = next_week_monday + datetime.timedelta(days=day_index)
                    schedule_data["start_date"] = target_date.strftime("%Y-%m-%d")
                    break
        
        monday_result = register_schedule(schedule_data, board_id)
        return f"일정 등록 완료: {monday_result}"
    except Exception as e:
        return f"오류 발생: {str(e)}"

iface = gr.Interface(
    fn=process_schedule_input,
    inputs=[
        gr.Textbox(label="Board ID", placeholder="monday.com의 보드 ID를 입력하세요"),
        gr.Textbox(lines=10, label="일정 입력", placeholder="일정을 자연어로 입력하세요")
    ],
    outputs=gr.Textbox(label="결과"),
    title="Gradio 일정 등록 봇",
    description="monday.com의 보드 ID와 일정을 입력하세요."
)

if __name__ == "__main__":
    iface.launch(share=True)