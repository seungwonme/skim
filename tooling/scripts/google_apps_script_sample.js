/**
 * @file google_apps_script_sample.js
 * @description SNS 크롤링 데이터를 Google Sheets에 저장하는 Apps Script 웹앱
 *
 * 이 스크립트는 Python 크롤러에서 보낸 HTTP POST 요청을 받아서
 * 구글 시트에 데이터를 저장합니다.
 *
 * 사용법:
 * 1. script.google.com에서 새 프로젝트 생성
 * 2. 이 코드를 Code.gs에 붙여넣기
 * 3. SPREADSHEET_ID 변수를 실제 시트 ID로 변경
 * 4. Deploy > New Deployment > Web app으로 배포
 * 5. 배포된 URL을 Python .env 파일의 GOOGLE_WEBAPP_URL에 설정
 */

// ⚠️ 여기에 실제 구글 시트 ID를 입력하세요!
const SPREADSHEET_ID = "1FXbcSw9TVqdz8Ou1iolEvD1nw8Nx9WrqVM4C_5Z46cA";

/**
 * HTTP POST 요청을 처리하는 메인 함수
 */
function doPost(e) {
  try {
    // JSON 데이터 파싱
    const data = JSON.parse(e.postData.contents);
    const posts = data.posts;
    const platform = data.metadata.platform;

    console.log(`📊 ${platform} 플랫폼에서 ${posts.length}개 게시글 수신`);

    // 스프레드시트 열기/생성
    const sheet = getOrCreateSheet(platform);

    // 헤더 설정 (처음에만)
    if (sheet.getLastRow() === 0) {
      const headers = [
        "작성자",
        "내용",
        "작성시간",
        "좋아요",
        "댓글",
        "공유",
        "조회수",
        "URL",
        "플랫폼",
      ];
      sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

      // 헤더 스타일링
      const headerRange = sheet.getRange(1, 1, 1, headers.length);
      headerRange.setBackground("#4285F4");
      headerRange.setFontColor("white");
      headerRange.setFontWeight("bold");
    }

    // 데이터 추가
    const rowsToAdd = posts.map((post) => [
      post.author || "",
      post.content || "",
      post.timestamp || "",
      post.likes || 0,
      post.comments || 0,
      post.shares || 0,
      post.views || 0,
      post.url || "",
      post.platform || platform,
    ]);

    // 데이터 한 번에 추가 (성능 최적화)
    if (rowsToAdd.length > 0) {
      const startRow = sheet.getLastRow() + 1;
      sheet.getRange(startRow, 1, rowsToAdd.length, 9).setValues(rowsToAdd);

      // 새로 추가된 행에 번갈아 색상 적용
      for (let i = 0; i < rowsToAdd.length; i++) {
        if ((startRow + i) % 2 === 0) {
          sheet.getRange(startRow + i, 1, 1, 9).setBackground("#F8F9FA");
        }
      }
    }

    // 성공 응답
    return ContentService.createTextOutput(
      JSON.stringify({
        success: true,
        message: `${posts.length}개 게시글이 ${platform} 시트에 저장되었습니다`,
        sheetUrl: SpreadsheetApp.openById(SPREADSHEET_ID).getUrl(),
        totalRows: sheet.getLastRow() - 1, // 헤더 제외
      }),
    ).setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    console.error("❌ 오류 발생:", error);
    return ContentService.createTextOutput(
      JSON.stringify({
        success: false,
        error: error.toString(),
      }),
    ).setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * 플랫폼별 시트를 가져오거나 생성합니다
 */
function getOrCreateSheet(platform) {
  try {
    const ss = SpreadsheetApp.openById(SPREADSHEET_ID);

    // 기존 시트 찾기
    let sheet = ss.getSheetByName(platform);

    // 시트가 없으면 새로 생성
    if (!sheet) {
      sheet = ss.insertSheet(platform);
      console.log(`📄 새 시트 생성: ${platform}`);
    }

    return sheet;
  } catch (error) {
    console.error(`❌ 시트 처리 중 오류: ${error}`);
    throw new Error(`시트 처리 실패: ${error.toString()}`);
  }
}

/**
 * 테스트용 HTTP GET 요청 처리
 */
function doGet(e) {
  return ContentService.createTextOutput(
    JSON.stringify({
      message: "SNS 크롤러 웹앱이 정상 작동 중입니다! 🚀",
      timestamp: new Date().toISOString(),
      spreadsheetId: SPREADSHEET_ID,
      availableSheets: getAvailableSheets(),
    }),
  ).setMimeType(ContentService.MimeType.JSON);
}

/**
 * 현재 사용 가능한 시트 목록을 반환합니다
 */
function getAvailableSheets() {
  try {
    const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
    return ss.getSheets().map((sheet) => sheet.getName());
  } catch (error) {
    return [`오류: ${error.toString()}`];
  }
}
