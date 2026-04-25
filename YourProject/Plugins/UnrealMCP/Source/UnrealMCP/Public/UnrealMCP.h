#pragma once

#include "Modules/ModuleManager.h"

class FUnrealMCPModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    FString GetServerScriptPath() const;
    void BootstrapPythonBridge();
};
