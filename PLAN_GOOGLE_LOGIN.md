# Plan sửa đăng nhập Google Flow / Veo 3

Ngày lập: 2026-10-02. Trạng thái: đã phân tích source và metadata local; chưa triển khai các thay đổi trong plan này.

## 1. Kết luận từ hiện trạng

Thư mục profile tồn tại không chứng minh phiên Google còn hiệu lực, Flow đã hoàn tất đăng nhập, hoặc ứng dụng đã lấy được token dùng cho Veo. Đây là các trạng thái cần theo dõi riêng.

Kiểm tra chỉ đọc tại thời điểm lập plan: kho Threads có 6 account, 0 account Google theo nhãn Google/Flow/Veo, 3 thư mục profile và 1 snapshot JSON. Chưa kiểm chứng snapshot còn hiệu lực. Không đọc/in giá trị cookies hoặc token để làm báo cáo.

Ảnh báo “Flow chưa trả access token từ profile đã chọn”. Thông báo này chỉ chứng minh worker chưa nhận được token trước khi kết thúc. Chưa đủ bằng chứng xác định Google đã logout hay chính xác endpoint nào lỗi. Bản sửa trước mới kiểm tra cú pháp; chạy import kiểm thử bị thiếu httpx, chưa kiểm thử đăng nhập thực tế từ đầu đến cuối.

### Những điểm đã xác nhận trong source

| Vị trí | Hành vi hiện tại | Tác động và việc cần sửa |
| --- | --- | --- |
| threads.py: GoogleLoginWorker.run | Chỉ giữ page ban đầu và kiểm tra context.pages[0] | Có thể bỏ lỡ tab/popup Flow sau đăng nhập; cần theo dõi tất cả trang thuộc context đăng nhập |
| GoogleLoginWorker.run | Bắt mọi lỗi launch persistent rồi mở context tạm | Lỗi thiếu browser hoặc lỗi profile bị báo thành profile bận; context tạm không cập nhật đầy đủ profile gốc |
| GoogleLoginWorker.run | success_sig và thêm account phụ thuộc token | Profile có thể tồn tại nhưng biến mất khỏi danh sách quản lý; cần record pending ngay khi tạo/liên kết profile |
| _probe_flow_session | Trả diagnostic nhưng worker bỏ qua _session_diag | Không phân biệt HTTP lỗi, JSON sai dạng, timeout, redirect hoặc session có email chưa có token |
| FLOW_SESSION_PROBE_JS | fetch không có AbortController/deadline | Một request treo có thể kéo dài toàn bộ vòng kiểm tra |
| GoogleLoginWorker.run | waited tăng theo sleep, không tính thời gian network | “10 phút” không phải timeout thời gian thực |
| _locked_google_headless_refresh | Luôn chạy fallback cookies sau probe profile kể cả result thành công | Mở browser thừa, giữ lock lâu; có thể trả kết quả snapshot khác thay vì kết quả profile mới |
| _check_token | Mọi non-200 trả False | Lỗi mạng, 429, 5xx hoặc thiếu quyền có thể bị trình bày như hết phiên; userinfo cũng chưa chứng minh quyền Veo/Ultra |
| _commit_google_login_result | Ghi account, registry, config lần lượt | Atomic từng file chưa phải transaction chung; cần xử lý ghi dở và phục hồi |
| grok_video_tool.py: _resolve_flow_shared_dir | Nhánh cuối trả GROK_AUTH_ROOT dù có auth_root của caller | Khi không tìm thấy profile, có nguy cơ dùng sai kho; phải giữ root được truyền hoặc báo thiếu profile |
| _run_veo_flow_single_attempt | Có nhánh gọi _google_profile_name_for_account(selected) thiếu auth_root | Phải truyền đúng kho Threads qua mọi bước resolve/refresh |
| google_auth_store.py: migrate_legacy_store | Copy registry giữ đường dẫn cũ; bỏ qua destination đã tồn tại | Cần kiểm tra đường dẫn sau migration và nhận diện copy dở; không coi folder tồn tại là migration hoàn tất |

Các khả năng chưa xác minh: người dùng đăng nhập ở Chrome cá nhân thay vì browser do tool mở; tab Flow chưa hoàn tất consent/onboarding; endpoint hoặc cấu trúc session thực tế khác source; profile đang bận; phiên Google bị thu hồi. Không kết luận nguyên nhân nào trong nhóm này trước khi quan sát phiên thực tế.

## 2. Kết quả cần đạt

- Account/profile luôn xuất hiện trong UI, kể cả đang chờ đăng nhập hoặc chưa lấy được token.
- Mở lại đúng profile đã chọn; nhận được phiên Flow của đúng tài khoản sau khi người dùng hoàn tất đăng nhập.
- Lưu kết quả thành công và mở lại app vẫn nhận đúng account, profile, project.
- Token hết hạn được làm mới từ phiên còn hợp lệ; chỉ yêu cầu người dùng đăng nhập khi có bằng chứng cần xác thực lại.
- Threads và Grok dùng cùng thư viện xử lý nhưng có kho dữ liệu riêng. Cùng email không có nghĩa là dùng chung browser profile.
- UI phân biệt đăng nhập Google, phiên Flow, token, quyền Veo và trạng thái bridge.
- Không báo hoàn thành chỉ dựa trên py_compile.

## 3. Thiết kế xử lý

### 3.1. Kho tài khoản và nhận diện profile

Giữ hai root hiện tại: ReviewThreads/data/google_flow_threads và data/google_flow_grok. Mọi hàm nhận auth_root tường minh; không fallback sang kho tool khác.

Dùng account_id và profile_id ổn định; email là thông tin đã xác minh, không dùng nickname làm bằng chứng danh tính. Lưu browser executable/channel, user_data_dir và profile directory thực tế. Kiểm tra tên/path để không thoát khỏi kho được quản lý.

Tạo record pending trước khi mở browser. Tác vụ khám phá profile chỉ đọc metadata, đưa profile chưa liên kết vào danh sách “Profile có sẵn — cần kiểm tra”. Giữ nguyên dữ liệu Google và tài khoản CLIProxy hiện có.

Một record có các trường: tool_id, account_id, profile_id, email đã xác minh, project_id, browser identity, trạng thái phiên, token_checked_at, last_error_code, revision. Credential nằm trong vùng lưu bí mật, UI/log chỉ hiển thị trạng thái. Không đưa token vào plan, test fixture hoặc log.

### 3.2. Các trạng thái và lỗi

Luồng chính: profile_known → browser_open → google_signed_in → flow_session_ready → token_ready → persisted → ready. Mỗi chuyển trạng thái cần bằng chứng tương ứng. Nếu chưa đủ bằng chứng thì giữ trạng thái trước, không suy ra logout.

Lỗi riêng: profile_busy, browser_missing, browser_launch_failed, wrong_account, user_action_required, flow_session_missing, token_missing, token_rejected, permission_denied, network_error, rate_limited, storage_write_failed, cancelled.

Google đã login nhưng chưa có Flow session phải hiện hướng dẫn hoàn tất Flow trong đúng cửa sổ. Token hợp lệ nhưng Veo không có quyền phải hiển thị lỗi quyền/quota, không bắt login lặp lại.

### 3.3. Browser và tab

- Chọn một browser/channel cố định cho profile; đăng nhập và refresh dùng cùng cấu hình, kể cả proxy nếu người dùng bật.
- Profile bận: hiển thị chủ tác vụ nếu biết, chờ có deadline và hỗ trợ hủy. Không tự chuyển sang context tạm rồi gọi đó là profile cũ.
- Theo dõi context page events, popup, navigation, close; chọn trang Flow phù hợp trong cùng context. Đóng tab đầu không đồng nghĩa kết thúc login khi tab Flow khác còn mở.
- Không đổi trang Google/2FA đang cần thao tác. Chỉ đọc phiên từ origin Flow hợp lệ và context đã chọn.
- Không kill Chrome toàn máy, không xóa Singleton locks của browser đang chạy. Mọi lệnh đóng browser chạy trong thread sở hữu Playwright.
- Giữ phiên browser đã đăng nhập kể cả lúc đọc token thất bại; không xóa cookie/profile để chữa token_missing.

### 3.4. Đọc session và token

Trước khi chốt adapter, quan sát origin, redirect, status, content-type và schema session của Flow thực tế. Chỉ ghi metadata đã che thông tin riêng tư; không dump response body, request Authorization hoặc cookies.

Ưu tiên phản hồi session mà chính tab Flow trong context đã chọn tải. Có thể đọc endpoint session đã xác nhận trong cùng context. Giới hạn host/path, số lần thử và timeout; không dò credential trên các tab không liên quan.

Parser xử lý schema đã quan sát bằng fixture giả lập. Không chấp nhận tùy ý trường token có thể là ID token/CSRF token. Payload lạ trả schema_unknown để chẩn đoán.

Dùng deadline time.monotonic và timeout từng request, fetch có AbortController. Tổng thời gian login, refresh, chờ lock tách riêng và đo theo thời gian thực. Refresh nền mặc định không mở UI đăng nhập.

Xác minh danh tính token theo cơ chế có sẵn và kiểm tra khả năng truy cập Flow/Veo bằng request chỉ đọc phù hợp sau khi xác nhận endpoint. Lỗi mạng/429/5xx không xóa phiên. 401 phải xác định nguồn trả lỗi. 403 cần phân loại quyền/challenge; không reset profile liên tục.

### 3.5. Lưu và phục hồi

- Profile persistent là nguồn phiên chính; snapshot là phương án phục hồi có metadata account/profile/revision.
- Chỉ snapshot sau khi xác minh đúng phiên; giữ bản tốt trước đó nếu ghi lỗi. Không coi chỉ có một cookie bất kỳ là bằng chứng đã login.
- Tách kết quả “phiên browser đã lưu” khỏi “token đã sẵn sàng”. Pending record phải tồn tại khi bước token thất bại.
- Account, registry, config cập nhật có revision và cơ chế commit/recovery; báo lỗi ghi rõ ràng. Tránh callback token cũ ghi đè kết quả login mới.
- Token refresh cập nhật đúng record đã gán cho job; không đổi account mặc định do refresh account khác.
- Migration kiểm tra mapping/path bên trong root, bản copy dở và file đã có. Lập manifest phục hồi, không tự overwrite profile đang mở.

### 3.6. Đồng thời và engine dùng chung

Tách module Google auth dùng chung cho UI Threads và engine Veo/Grok. Tool_id/auth_root là tham số bắt buộc tại boundary. UI Qt chỉ nhận events; dịch vụ auth xử lý browser, token và persistence.

Lock theo đường dẫn profile thực đã chuẩn hóa. Manual login có ưu tiên; refresh nền có thể hủy tại các checkpoint. Nhiều job cùng hết token chia sẻ một lần refresh, sau đó đọc revision mới.

Sửa return ngay khi probe profile thành công. Chỉ dùng snapshot sau lỗi phù hợp và kiểm tra đúng account. Không có fallback tự đổi root, đổi tài khoản hoặc biến lỗi mạng thành đăng xuất.

## 4. Các giai đoạn triển khai

### P0 — Tái hiện và quan sát

1. Xác định Python/venv thực dùng để mở Threads và kiểm tra httpx, PyQt6, Playwright, browser binary.
2. Xác định bản threads.py đang chạy và auth root thực tế; tránh test source mới trong khi UI là tiến trình cũ.
3. Ghi log có cấu trúc, không chứa bí mật: attempt_id, stage, profile_id, tab origin/path đã lọc, HTTP status, schema keys cho phép, elapsed, lock owner, persistence result.
4. Tái hiện một lần login từ nút Google Flow, theo dõi tab và trạng thái session. Chưa tạo video tính phí ở bước này.

Đầu ra: biết bước đầu tiên thất bại, browser/profile thực tế và nguyên nhân cụ thể; nếu chưa biết phải ghi rõ thiếu bằng chứng nào.

### P1 — Sửa lỗi xác định được từ code

1. Tạo pending account và khám phá profile có sẵn.
2. Sửa theo dõi tab và phân loại launch error.
3. Thêm deadline thực, diagnostic, hủy đúng thread.
4. Return ngay khi refresh profile thành công.
5. Sửa toàn bộ truyền auth_root và fallback root của engine.
6. Dừng mở lại browser thừa; bảo vệ kết quả login mới khỏi refresh cũ.

### P2 — Hợp nhất auth và persistence

1. Module dùng chung, state model, adapter session dựa trên bằng chứng P0.
2. Mapping account/profile bền vững, commit/recovery, snapshot có revision.
3. Tích hợp UI login/relogin, kiểm tra một account, startup, định kỳ, engine ảnh/video và credits.
4. UI hiển thị bước đang thực hiện, lỗi cụ thể, “Mở profile”, “Kiểm tra phiên”, “Đăng nhập lại”.

### P3 — Kiểm thử và nghiệm thu

| Tình huống | Điều kiện đạt |
| --- | --- |
| Login mới | Record pending xuất hiện ngay; sau xác minh chuyển ready và lưu đầy đủ |
| Profile có sẵn chưa có account | Liên kết được, không bắt tạo profile khác |
| Đóng/mở lại app hai lần | Cùng account/profile, token và session được đánh giá đúng |
| Login chuyển popup/tab mới | Vẫn nhận được Flow session; đóng tab đầu không làm hỏng |
| Profile bận | Báo đúng lỗi, không mở context tạm mất trạng thái |
| Token hết hạn, session còn tốt | Refresh một lần, job dùng token mới; không cần login lặp |
| Session thực sự hết hạn | Giữ account và profile, chuyển user_action_required |
| Mạng lỗi, 429, 5xx | Giữ credential, báo đúng lỗi và backoff có giới hạn |
| 403/quota | Phân biệt quyền/quota và yêu cầu xác thực; không reset phiên vô cớ |
| Sai tài khoản | Không ghi đè account đang được chọn |
| Nhiều worker cùng refresh | Một tác vụ refresh/profile, các job dùng chung kết quả mới |
| Grok và Threads cùng email | Root riêng, không ghi vào kho nhau; cùng profile thực vẫn phải khóa |
| Lỗi ghi file/đóng app giữa chừng | Không báo ready giả; phục hồi được bản đã commit |
| Migration dở | Phát hiện thiếu dữ liệu; không đánh dấu thành công vì folder đã tồn tại |

Unit test dùng token/cookie giả cho parser, state transitions, timeout, root isolation, refresh deduplication và lỗi ghi. Integration test dùng thư mục tạm, kiểm tra restart và khóa giữa tiến trình. Test thực tế dùng đúng runtime của app và tài khoản người dùng; không xuất bí mật ra log.

Sau khi login và kiểm tra chỉ đọc đạt: chạy một job Veo nhỏ theo cấu hình được người dùng chọn để kiểm tra engine nhận cùng account/token, báo rõ nếu bước này chưa chạy hoặc cần chọn project. Chỉ ghi nghiệm thu hoàn tất khi quan sát đủ kết quả; không suy ra hoạt động từ compile pass.

## 5. Thứ tự file dự kiến

- ReviewThreads/threads.py: UI, login workers, account records, callback persistence và trạng thái.
- grok_video_tool.py: boundary auth_root, resolve profile, engine refresh; các thay đổi giữ tương thích callers Grok.
- google_auth_store.py: manifest, mapping, migration, phục hồi ghi dở.
- Module mới dự kiến google_flow_auth.py: browser/session/token/state/lock dùng chung; tên cuối có thể điều chỉnh theo cấu trúc test.
- Tests và docs chẩn đoán trong phạm vi repo; dữ liệu thật giữ nguyên trong kho từng tool.

## 6. Tiêu chí hoàn tất

Người dùng login một lần trong đúng profile do tool quản lý, thấy account được nhận, đóng/mở lại tool vẫn thấy account và khôi phục token khi Google session còn hợp lệ. Khi không thể tự khôi phục, UI chỉ rõ bước và nguyên nhân, giữ dữ liệu phiên, hướng dẫn đúng thao tác cần làm. Báo cáo cuối phân biệt kiểm thử giả lập, kiểm thử đăng nhập thực tế và job Veo thực tế.

## 7. Bản sửa đã triển khai — 2026-10-03

### Cập nhật sau ảnh lỗi Google từ chối trình duyệt

Ảnh thực tế báo `accounts.google.com/v3/signin/rejected`, “This browser or app may not be secure” và banner browser bị automation điều khiển. Đây là lỗi trước bước cấp token; 30 test cũ không kiểm tra việc Google chấp nhận login thật. Không dùng kết quả test giả lập để tuyên bố đăng nhập thật hoạt động.

Bản tiếp theo đã đổi login tương tác thành Chrome được cài trên Windows, mở bằng tiến trình thường, không Playwright/CDP trong giai đoạn người dùng đăng nhập, không tắt bảo mật hoặc giả UA. Người dùng vào Flow rồi đóng riêng cửa sổ này; chỉ sau đó tool mở phiên kiểm tra headless bằng cùng Chrome. Trình duyệt kiểm tra có thể vẫn bị Google yêu cầu xác thực lại — không bảo đảm khôi phục được nếu Google thu hồi phiên.

Để không thay đổi dữ liệu Chromium/encryption cũ, mỗi tool dùng `<auth_root>/native_profiles/<profile>` cho Chrome mới, giữ nguyên `<auth_root>/profiles/<profile>` cũ. Mapping ghi trong `browser_settings/<profile>.json`. Không coi snapshot Chromium cũ là snapshot đã xác minh của Chrome mới. Hủy hoặc timeout chờ đăng nhập không kill cửa sổ do người dùng đang thao tác.

Đã bổ sung mã lỗi `google_signin_rejected` và `flow_token_unavailable`: trường hợp thứ hai không đồng nghĩa Google logout. Threads desktop dùng CaptchaBridge và extension riêng UpProject Captcha Bridge, không phải FlowKit của tool Web. Không chuyển engine sang FlowKit trong phạm vi sửa login.

Kết quả cập nhật: **37/37 test đạt** (thêm kiểm tra lệnh mở Chrome không automation/debugging, giữ nguyên data cũ, hủy không kill Chrome, cách ly snapshot, phát hiện rejected URL, resolver native profile và không mở Playwright khi login thủ công chưa hoàn tất). Login thật vẫn phải nghiệm thu riêng.

Nguồn: [Google — đăng nhập bằng trình duyệt được hỗ trợ](https://support.google.com/accounts/answer/7675428?hl=en).

### Theo dõi lần kiểm thử Chrome thường

- Người dùng xác nhận đã vào được Flow trong Chrome thường, nhưng chưa đóng cửa sổ ở thời điểm phản hồi. Đây mới là xác nhận bước đăng nhập qua UI; chưa chứng minh token hoặc khả năng khôi phục phiên sau khi đóng Chrome.
- Thêm `auth_attempts/<profile>.json`: chỉ lưu ID lần thử, mã giai đoạn và thời gian. Không lưu token, cookie, query URL hoặc nội dung lỗi tùy ý. Bảng tài khoản hiển thị giai đoạn gần nhất, không gọi mọi lỗi là “cần login lại”.
- Khi người dùng hoàn tất gần cuối 10 phút chờ đăng nhập, bước kiểm tra mặc định vẫn được đủ 55 giây riêng, tránh timeout ngay sau khi vừa đóng Chrome.
- **39/39 kiểm thử đạt** sau thay đổi trạng thái. Vẫn cần kiểm tra thật sau khi đóng cửa sổ đăng nhập; chưa nghiệm thu engine tạo video với Flow host mới.

### Kết quả sau khi người dùng đóng Chrome

Đã mở lại đúng `native_profiles/leviettung04` bằng Chrome cài trên máy: Flow ở trang làm việc `/`, có liên kết SignOutOptions và danh sách dự án. Đây là bằng chứng phiên trình duyệt được giữ sau khi đóng; chưa phải xác minh email OAuth hay quyền tạo video.

Hai endpoint session trên `flow.google.com` trả HTTP 200 HTML; endpoint `labs.google/fx/api/auth/session` trả JSON `{}`. Không nhận được Bearer token. Đã thêm mã `flow_transport_required` và trạng thái UI tương ứng; khi có bằng chứng phiên Flow còn, không thử lại snapshot cũ hoặc yêu cầu login lại. Tên mã lỗi được giữ để tương thích với các nhánh xử lý hiện tại; không có nghĩa phải cài FlowKit. Việc lấy Bearer và nghiệm thu video chưa hoàn tất; không bật account thành API-ready bằng một token giả.

### Đính chính extension và hướng sửa

- `ReviewThreads/threads.py` import `CaptchaBridge` từ `grok_video_tool.py` và bọc thành `ReviewCaptchaBridge` để tuần tự hóa reset/token reCAPTCHA.
- Source `Extension Recapcha/veo3-captcha-extension-2026-07-20-13-59` có manifest UpProject Captcha Bridge 1.9.0. Cần xác minh đường dẫn extension thực sự được Chrome nạp trước khi sửa hoặc yêu cầu reload.
- Extension hiện xử lý lệnh captcha/reset qua `/api/internal/captcha/*`; token trả về là reCAPTCHA, không phải Bearer tài khoản. Không lưu token này vào trường `api_key` Google.
- Profile Chrome giữ phiên đăng nhập và Bearer phục vụ API là hai điều kiện khác nhau. Chưa lấy được Bearer không đủ để kết luận cookies bị mất.
- Hướng tiếp theo: kiểm tra luồng lấy Bearer trong đúng profile/tài khoản; xác minh email và hiệu lực trước khi lưu. Không lấy nhầm phiên tài khoản master của extension cho tài khoản render khác.
- Không thay extension riêng bằng FlowKit, không xóa profile hoặc ép login lại khi phiên còn; không tuyên bố đã sửa xong lấy token khi chưa kiểm chứng.

### Bằng chứng thực tế

- Đã dùng đúng Python 3.11 cài trên máy với PyQt6/httpx/Playwright của tool.
- Trong lần kiểm tra profile ứng dụng, `flow.google.com` chuyển về `/about`; các URL session trên host mới trả HTTP 200 nhưng là HTML. Session legacy `labs.google/fx/api/auth/session` trả JSON rỗng. Không được diễn giải HTTP 200 hoặc folder profile tồn tại thành đăng nhập Flow thành công.
- Extension có sẵn trong repo quan sát Authorization Bearer của request tới `aisandbox-pa.googleapis.com`. Cơ chế mới quan sát cùng loại request trong đúng browser context/profile; xác minh email qua Google userinfo trước khi lưu.
- Đã tìm thấy hai nhánh Chrome Spy trong Grok gọi `taskkill /f /im chrome.exe`. Đây là rủi ro đóng nhầm mọi profile, dù kho dữ liệu hai tool đã tách. Đã thay bằng báo cổng bận và dừng thao tác đó, không tự kill Chrome.
- Phiên kiểm thử đăng nhập thật đã mở browser profile `leviettung04` rồi kết thúc mà chưa tạo metadata phiên được xác minh. Chưa có bằng chứng login thật thành công, không đánh dấu nghiệm thu end-to-end.

### Thay đổi

1. `google_flow_auth.py`: parser access-token rõ ràng; bỏ generic token/id_token; quan sát request/response toàn context và nhiều tab; deadline; hủy ở worker sở hữu browser; cùng cấu hình browser/proxy cho login/refresh/credits.
2. Không fallback sang context tạm khi mở profile lỗi; giữ nguyên profile/snapshot đã có. Phân biệt HTTP 401, 403, 429, mạng, sai email, lỗi lưu và browser bận. Không tự vượt CAPTCHA hoặc bước xác thực Google.
3. Tách root Threads/Grok, khóa profile và khóa commit liên tiến trình, gộp tác vụ refresh cùng profile trong một tiến trình. Không fallback root Threads sang Grok.
4. Account pending cho profile Threads đã có; chọn profile hiện hữu khi đăng nhập. Token Google được che trên UI. File tài khoản hỏng không bị ghi đè thành danh sách rỗng.
5. Commit tài khoản/registry/config có journal chỉ chứa fingerprint; phục hồi ghi dở. Revision/fingerprint chống kết quả refresh cũ ghi đè login mới. Save từ manager cũ giữ tài khoản mới được thêm bên ngoài.
6. Engine và UI dùng kết quả đã xác minh của worker, không mở lại browser/kiểm tra userinfo thừa; engine không downgrade sang cookie header khi token đã bị từ chối. Engine lưu refresh qua commit chung.
7. Migration mới dùng thư mục staging rồi rename; lỗi giữa chừng không công bố profile thiếu dữ liệu. Migration partial của bản cũ giữ trạng thái cần xử lý, không tự xóa hay ghi đè profile. Mapping registry chỉ trỏ vào kho đích đã tồn tại. Kiểm tra file nguồn thay đổi là bảo vệ bổ sung, không bảo đảm snapshot nhất quán nếu người dùng vẫn mở browser nguồn; khi migration báo partial phải đóng đúng browser nguồn rồi kiểm tra lại.

### Kiểm thử và cách chạy lại

Kết quả lần chạy cuối: **30/30 kiểm thử đạt**, gồm 4 kiểm thử Chromium thật với dữ liệu/network giả lập. Import hai tool và kiểm tra cú pháp đều đạt. Đây không phải xác nhận đã đăng nhập thành công tài khoản Google thật.

Chạy từ thư mục Grok:

```powershell
& 'C:\Users\ADMIN\AppData\Local\Programs\Python\Python311\python.exe' -m unittest discover -s scratch -p 'test_google*.py' -v
```

`scratch/test_google_flow_auth.py` kiểm tra parser/identity/HTTP, Windows lock, gộp refresh, commit/recovery, migration partial, store lỗi, restart account manager, engine nhận token mới và tách root.

`scratch/test_google_browser_flow.py` dùng Chromium thật, profile tạm và route toàn bộ request bằng dữ liệu giả: token tới từ tab mới, sai email, hủy, lỗi lưu. Không gửi token giả lên Google, không tạo ảnh/video.

### Nghiệm thu còn cần tài khoản thật

1. Đóng/mở lại Threads để nạp source mới; không tự tắt app đang chạy vì có thể còn job chưa lưu.
2. Vào Account Manager → chọn Google Flow/Veo → Login → chọn chính profile đã đăng nhập. Không nhầm phiên Chrome cá nhân với profile riêng của ứng dụng.
3. Trong cửa sổ đó, hoàn tất xác thực Google và mở ứng dụng Flow (không dừng ở trang giới thiệu). Chờ tool báo đã xác minh và lưu; nếu chưa được, cung cấp mã lỗi/status mới, không gửi token/cookie.
4. Đóng/mở app hai lần, kiểm tra account vẫn tồn tại; chạy Check token. Token còn hiệu lực và refresh từ session là hai phép thử khác nhau; không cần cố tình xóa token/cookie thật để thử.
5. Chưa chạy job Veo trả phí ở đợt kiểm tra này. Chỉ nghiệm thu video thực sau khi xác nhận đúng tài khoản/project và cấu hình job.

Giới hạn: bản sửa không thể bảo đảm Google không hết hạn hoặc thu hồi phiên. Token/cookie vẫn là dữ liệu bí mật trong kho local hiện hữu; không đưa chúng vào docs, log hoặc dữ liệu training. Những script `scratch/integrate_*.py` là migration source một lần phục vụ đợt sửa, không chạy lại chúng khi sử dụng tool.
