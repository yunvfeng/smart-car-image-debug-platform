#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show)
{
    (void)instance;
    (void)previous;
    (void)arguments;
    (void)show;
    wchar_t directory[32768];
    wchar_t interpreter[32768];
    wchar_t command[32768];
    DWORD length = GetModuleFileNameW(NULL, directory, 32768);
    if (!length || length >= 32768) {
        MessageBoxW(NULL, L"Unable to locate the application folder.", L"VisionLab", MB_ICONERROR);
        return 1;
    }
    wchar_t *separator = wcsrchr(directory, L'\\');
    if (!separator) {
        return 1;
    }
    *separator = L'\0';
    int result = swprintf(interpreter, 32768, L"%ls\\runtime\\pythonw.exe", directory);
    if (result < 0 || result >= 32768) {
        return 1;
    }
    result = swprintf(command, 32768, L"\"%ls\" \"%ls\\scripts\\launch_app.pyw\"", interpreter, directory);
    if (result < 0 || result >= 32768) {
        return 1;
    }
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    if (!CreateProcessW(interpreter, command, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, directory, &startup, &process)) {
        MessageBoxW(NULL, L"Unable to start VisionLab. Extract the complete ZIP, then open VisionLab.exe.",
                    L"VisionLab", MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
