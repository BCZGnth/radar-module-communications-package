EXEC       = xc8.exe
CXXFLAGS   = -Wall -c --chip=18f46k42 
IDIRS      = ./Inc ./Src ./Drivers/BSP/Components/vl53l1x_uld 
INCD_PARAMS=$(foreach d, $(IDIRS), -I$d)
OBJS       = $(SRCS:.cc=.o)

default : $(EXEC)
	$(EXEC) $(CXXFLAGS) ./Src/main.c $(INCD_PARAMS) 

$(EXEC) : $(OBJS)