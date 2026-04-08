#!/bin/bash
# SRT 자막 파일을 깔끔한 TXT로 변환
# - 번호/타임스탬프 제거
# - 중복 라인 제거 (자동 자막의 겹치는 구간 처리)
# - 타임스탬프 마커 삽입 (1분 간격)
# Usage: ./srt_to_txt.sh <input.srt> [output.txt]

INPUT="$1"
OUTPUT="${2:-${INPUT%.srt}.txt}"

if [ -z "$INPUT" ] || [ ! -f "$INPUT" ]; then
  echo "Usage: $0 <input.srt> [output.txt]"
  exit 1
fi

awk '
BEGIN { prev = ""; last_min = -1 }

# 타임스탬프 라인에서 시작 시간 추출
/^[0-9]{2}:[0-9]{2}:[0-9]{2}/ {
  split($1, t, ":")
  cur_min = t[1] * 60 + t[2]
  next
}

# 숫자만 있는 라인 (시퀀스 번호) 스킵
/^[0-9]+$/ { next }

# 빈 라인 스킵
/^[[:space:]]*$/ { next }

# 텍스트 라인 처리
{
  # HTML 태그 제거
  gsub(/<[^>]+>/, "")
  # 앞뒤 공백 제거
  gsub(/^[[:space:]]+|[[:space:]]+$/, "")

  if ($0 == "" || $0 == prev) next

  # 1분 간격 타임스탬프 마커 삽입
  if (cur_min >= 0 && cur_min != last_min && cur_min % 1 == 0 && (last_min == -1 || cur_min > last_min)) {
    h = int(cur_min / 60)
    m = cur_min % 60
    printf "\n[%02d:%02d]\n", h, m
    last_min = cur_min
  }

  print
  prev = $0
}
' "$INPUT" > "$OUTPUT"

# 결과 통계
input_lines=$(wc -l < "$INPUT")
output_lines=$(wc -l < "$OUTPUT")
output_size=$(wc -c < "$OUTPUT" | tr -d ' ')

echo "변환 완료: $OUTPUT"
echo "  SRT: ${input_lines} lines → TXT: ${output_lines} lines ($(echo "scale=0; $output_size/1024" | bc)KB)"
